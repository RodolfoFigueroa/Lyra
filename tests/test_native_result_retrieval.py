"""Client tables preserve captured native contracts and independent ownership."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pandera.engines.geopandas_engine import Geometry
from pyarrow import ipc, types
from shapely.geometry import Point

# Exercise client decoding independently of HTTP transport.
from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.api.exceptions import DownloadError
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import TableJobResult
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.local import LocalRunner
from lyra.runtime.result_descriptions import (
    ResultDescriptions,
)
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import PluginDefinition, metric
from lyra.sdk.output_schema import compile_output
from tests.job_id_helpers import TEST_JOB_ID
from tests.retained_table_helpers import retained_service
from tests.sqlite_helpers import database

__all__ = ["database"]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("state", ["populated", "empty", "all-null", "duplicates"])
@pytest.mark.parametrize("spatial", [False, True])
def test_declared_types_and_independent_reads(
    tmp_path: Path, state: str, *, spatial: bool
) -> None:
    values = {
        "count": pd.Series([2**60 + 1, None], dtype="Int64"),
        "ratio": pd.Series([1.5, None], dtype="Float64"),
        "flag": pd.Series([True, None], dtype="boolean"),
        "label": pd.Series(["first", None], dtype="string"),
    }
    frame = pd.DataFrame(values)
    if state == "all-null":
        frame.iloc[:] = pd.NA
    if spatial:
        frame = gpd.GeoDataFrame(
            frame,
            geometry=gpd.GeoSeries([Point(1000, 2000), Point(3000, 4000)]),
            crs=3857,
        )
    if state == "duplicates":
        frame = pd.concat([frame, frame], ignore_index=True)
    if state == "empty":
        frame = frame.iloc[:0]
    schema = pa.DataFrameSchema(
        {
            name: pa.Column(
                Geometry(crs=3857) if name == "geometry" else series.dtype,
                nullable=True,
                description=str(name),
                checks=pa.Check(
                    lambda values: values.dropna().is_valid.all(), name="valid_geometry"
                )
                if name == "geometry"
                else None,
                metadata={"unit": None},
            )
            for name, series in frame.items()
        },
        strict=True,
    )
    _, description = compile_output(schema)
    path = tmp_path / "results.sqlite"
    service, key = retained_service(path, schema, frame)
    assert_batched_frame(path, key, frame)
    observation = service.read_observation(key)
    assert observation is not None
    result = service.read_result(observation)
    assert isinstance(result, TableJobResult)
    inspection = service.inspect(observation)
    assert isinstance(inspection, TerminalResultOutput)
    check_inspection(
        inspection, len(frame), populated=state in {"populated", "duplicates"}
    )
    if state in {"populated", "duplicates"}:
        check_json_cells(result, spatial=spatial)
    with ResultStore.open(path) as store:
        stored_tables = store.read_result(key).tables
    original = result.model_dump_json()
    for tables in [decode_tables(result), stored_tables]:
        restored = tables["rows"]
        assert_retrieved_frame(restored, frame)
        pd.testing.assert_series_equal(
            restored["count"], frame["count"], check_exact=True
        )
        restored.attrs["scope"] = {"provider": "forged"}
        if len(restored):
            restored.loc[0, "count"] = 99
        del restored["flag"]
    assert_retrieved_frame(decode_tables(result)["rows"], frame)
    with ResultStore.open(path) as store:
        pd.testing.assert_frame_equal(store.read_table(key, "rows"), frame)
    assert result.model_dump_json() == original
    result.output_metadata["rows"].columns.clear()
    repeated = service.read_result(observation)
    assert isinstance(repeated, TableJobResult)
    assert repeated.output_metadata["rows"] == description
    assert_retrieved_frame(decode_tables(repeated)["rows"], frame)


@pytest.mark.usefixtures("database")
def test_reopened_string_schema_is_independent_of_pandas_storage(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame({"label": pd.Series(["001", None], dtype="string")})
    schema = pa.DataFrameSchema(
        {
            "label": pa.Column(
                "string", nullable=True, description="Label", metadata={"unit": None}
            )
        },
        strict=True,
    )
    path = tmp_path / "strings.sqlite"
    _, reference = retained_service(path, schema, frame)
    schemas = []
    for storage in ("python", "pyarrow"):
        with (
            pd.option_context("mode.string_storage", storage),
            ResultStore.open(path) as reader,
            ipc.open_stream(b"".join(reader.stream_table(reference, "rows"))) as stream,
        ):
            schemas.append(stream.schema)
            assert types.is_string(stream.schema.field("label").type)
            table = stream.read_all()
            assert table["label"].to_pylist() == ["001", None]
            decoded = table.to_pandas().label
            assert isinstance(decoded.dtype, pd.StringDtype)
            pd.testing.assert_series_equal(decoded, frame.label.astype(decoded.dtype))
    assert schemas[0].equals(schemas[1], check_metadata=True)


def assert_batched_frame(path: Path, key: str, frame: pd.DataFrame) -> None:
    with ResultDescriptions(SQLiteStore(path)).select(key, "rows") as selected:
        batches = list(selected.batches(1))
        assert sum(map(len, batches)) == len(frame)
        assert selected.owner_scope is None
        assert [column.name for column in selected.registration.columns] == list(frame)
        assert_retrieved_frame(selected.frame(), frame)


def test_malformed_declared_columns_raise_useful_error() -> None:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})},
        strict=True,
    )
    result = TableJobResult(
        job_id="f" * 32,
        outputs={"rows": [{"unexpected": 1}]},
        output_metadata={"rows": compile_output(schema)[1]},
    )
    with pytest.raises(DownloadError, match="invalid output 'rows'"):
        decode_tables(result)


def assert_retrieved_frame(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Allow only the coordinate rounding from GeoJSON CRS reprojection."""
    if isinstance(expected, gpd.GeoDataFrame):
        assert isinstance(actual, gpd.GeoDataFrame)
        assert_geodataframe_equal(actual, expected, check_less_precise=True)
        pd.testing.assert_frame_equal(
            actual.drop(columns="geometry"),
            expected.drop(columns="geometry"),
            check_exact=True,
        )
    else:
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(
    ("declared", "native"),
    [
        ("int64", "Int64"),
        ("float64", "Float64"),
        ("bool", "boolean"),
        ("str", "string"),
    ],
)
def test_nullable_scalar_reconstruction(
    declared: str, native: str, *, empty: bool
) -> None:
    result = TableJobResult(
        job_id=TEST_JOB_ID,
        outputs={"rows": [] if empty else [{"value": None}]},
        output_metadata={
            "rows": OutputMetadata(
                columns=[
                    ColumnMetadata(
                        name="value",
                        dtype=declared,
                        nullable=True,
                        unit=None,
                        description="Value",
                    )
                ]
            )
        },
    )
    restored = decode_tables(result)["rows"]
    assert str(restored.value.dtype) == native
    assert restored.value.isna().all()
    assert len(restored) == (0 if empty else 1)


@pytest.mark.usefixtures("database")
def test_private_storage_names_do_not_replace_declared_columns(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            name: pd.Series(["equal", "equal", "equal"], dtype="string")
            for name in ("value_key", "_value_key", "row_index", "_row_index")
        }
    )
    schema = pa.DataFrameSchema(
        {
            name: pa.Column("string", description=str(name), metadata={"unit": None})
            for name in frame
        },
        strict=True,
    )
    path = tmp_path / "results.sqlite"
    _, reference = retained_service(path, schema, frame)
    with ResultDescriptions(SQLiteStore(path)).select(reference, "rows") as selected:
        batches = list(selected.batches(1))
        assert [batch[0] for batch in batches] == [("equal",) * 4] * 3
        pd.testing.assert_frame_equal(selected.frame(), frame)
    with ResultStore.open(path) as store:
        pd.testing.assert_frame_equal(store.read_table(reference, "rows"), frame)


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("empty", [False, True])
def test_geometry_only_native_and_arrow_after_reopen(
    tmp_path: Path, *, empty: bool
) -> None:
    schema = pa.DataFrameSchema(
        {
            "geometry": pa.Column(
                Geometry(crs=3857),
                nullable=True,
                description="Survey geometry",
                metadata={"unit": None},
                checks=pa.Check(lambda s: s.is_valid, name="valid_geometry"),
            )
        },
        strict=True,
    )
    frame = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries(
            [] if empty else [Point(1, 2, 3), Point(), None], crs=3857
        )
    )
    path = tmp_path / "geometry.sqlite"

    @metric(name="survey", description="Survey geometries", outputs={"rows": schema})
    def survey() -> gpd.GeoDataFrame:
        return frame

    runner = LocalRunner.from_definitions(
        {"survey": PluginDefinition(metrics=[survey])}, result_store_path=path
    )
    reference = runner.run("survey").report.job_id
    assert reference is not None
    path.touch()
    for _ in range(2):
        with ResultStore.open(path) as reader:
            restored = reader.read_table(reference, "rows")
            with ipc.open_stream(
                b"".join(reader.stream_table(reference, "rows"))
            ) as stream:
                downloaded = gpd.GeoDataFrame.from_arrow(stream.read_all())
        for native in (restored, downloaded):
            assert isinstance(native, gpd.GeoDataFrame)
            assert list(native) == ["geometry"]
            assert native.crs == frame.crs
            assert native.geometry.to_wkb().tolist() == frame.geometry.to_wkb().tolist()


@pytest.mark.usefixtures("database")
def test_case_colliding_structural_columns_round_trip(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        {
            "ROW_KEY": ["x", "y", "z"],
            "row_key": ["1", "2", "1"],
            "LOCATION_ID": ["north", "south", "east"],
            "location_id": ["a", "a", "b"],
        },
        dtype="string",
    )
    declaration = pa.DataFrameSchema(
        {
            name: pa.Column("string", description=name, metadata={"unit": None})
            for name in frame.columns
        },
        strict=True,
        unique=["location_id", "row_key"],
    )
    service, reference = retained_service(
        tmp_path / "tables.sqlite",
        declaration,
        frame,
        owner=ProviderScope(provider_id="places"),
    )
    with ResultStore.open(service.result_store_path) as store:
        restored = store.read_table(reference, "rows")
        pd.testing.assert_frame_equal(restored, frame)


def check_inspection(
    inspection: TerminalResultOutput, rows: int, *, populated: bool
) -> None:
    assert inspection.outputs["rows"].metadata is not None
    assert inspection.outputs["rows"].metadata.row_count == rows
    summary = inspection.outputs["rows"].metadata.summary
    assert (
        summary["count"].minimum
        == summary["count"].maximum
        == (2**60 + 1 if populated else None)
    )
    assert summary["flag"].minimum is summary["flag"].maximum is None


def check_json_cells(result: TableJobResult, *, spatial: bool) -> None:
    encoded = result.model_dump(mode="json")["outputs"]["rows"]
    records = (
        [feature["properties"] for feature in encoded["features"]]
        if spatial
        else encoded
    )
    assert type(records[0]["count"]) is int
    assert records[0]["count"] == 2**60 + 1
    assert records[0]["flag"] is True
    assert records[1]["count"] is records[1]["flag"] is None
