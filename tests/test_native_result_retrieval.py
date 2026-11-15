"""Client tables preserve captured native contracts and independent ownership."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

# Exercise client decoding independently of HTTP transport.
from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.api.exceptions import DownloadError
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultDescriptions,
    ResultOutput,
)
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.output_schema import compile_output
from lyra.sdk.storage_schema import describe
from lyra.server.job_store import JobObservation
from lyra.server.results import ResultService
from test_support.publication import publish_result
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
    observation = service.read_observation("native")
    assert observation is not None
    result = service.read_result(observation)
    assert isinstance(result, TableJobResult)
    inspection = service.inspect(observation)
    assert isinstance(inspection, TerminalResultOutput)
    assert inspection.outputs["rows"].metadata is not None
    assert inspection.outputs["rows"].metadata.row_count == len(frame)
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
    assert observation.retained_result is not None
    observation.retained_result.metadata.outputs["rows"].table_schema.columns.clear()
    repeated = service.read_observation("native")
    assert repeated is not None
    assert repeated.retained_result is not None
    assert repeated.retained_result.metadata.outputs["rows"].table_schema == description
    assert_retrieved_frame(decode_tables(service.read_result(repeated))["rows"], frame)


def test_malformed_declared_columns_raise_useful_error() -> None:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})},
        strict=True,
    )
    result = TableJobResult(
        execution_id="b" * 32,
        job_id="invalid",
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


def retained_service(
    path: Path, schema: pa.DataFrameSchema, frame: pd.DataFrame
) -> tuple[ResultService, str]:
    """Publish through isolated relational boundaries for remote retrieval."""
    writer = SQLiteStore(path)
    layout = describe(schema, metadata=compile_output(schema)[1])
    MetricTables(writer).publish(
        Header(value_key="value", node_id="native", output_scope="unpartitioned"),
        {"rows": frame},
        {"rows": layout},
    )
    registration = next(iter(writer.layout.descriptions.values()))
    key = publish_result(
        ResultDescriptions(writer),
        ResultDescription(
            metric_name="native",
            output_names=("rows",),
            parameters={},
            outputs={
                "rows": ResultOutput(
                    registration_key=registration.key,
                    owner_scope=None,
                    computations=(ComputationSelection(value_key="value"),),
                )
            },
        ),
    )
    path.touch()
    now = datetime.now(UTC)
    jobs = Mock(
        read_job=Mock(
            return_value=JobObservation(
                snapshot=JobStatusInfo(
                    job_id="native", status="succeeded", created_at=now, updated_at=now
                ),
                result_ref=key,
                execution_id=ResultDescriptions(writer)
                .read_complete(key)[1]
                .execution_id,
            )
        )
    )
    service = ResultService(jobs, path)
    return service, key


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
        execution_id="b" * 32,
        job_id="job",
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
