"""Managed local exports preserve retained values and commit files atomically."""

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import IO, TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas.testing import assert_geodataframe_equal
from pandera.engines.geopandas_engine import Geometry
from pydantic import ValidationError
from shapely.geometry import Point

from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.data import geoparquet
from lyra.runtime import provider_acquisition
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.output_schema import compile_output
from test_support.publication import publish_result
from tests.export_lifecycle_helpers import inject_failure
from tests.location_input_helpers import publish_inputs, retained
from tests.result_description_helpers import publish_values
from tests.retained_table_helpers import retained_service
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from lyra.contracts.types import JsonObject

__all__ = ["database"]


@pytest.mark.parametrize("keys", [(), ("one", "two")])
@pytest.mark.parametrize(
    "selection", [("a", False), ("a", True), ("b", False), ("b", True)]
)
def test_location_export(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    selection: tuple[str, bool],
) -> None:
    name, string_path = selection
    writer = SQLiteStore(tmp_path / "store.sqlite")
    retained = publish_values(writer, keys, spatial=True)
    reference = publish_result(ResultDescriptions(writer), retained)
    reader = ResultStore(writer)
    description = reader.describe(reference)
    expected = reader.read_table(reference, name)
    edited = reader.read_table(reference, name)
    edited["value"] = 999
    edited.attrs["lyra"] = {"job_id": "forged"}
    changed = reader.describe(reference)
    changed.outputs[name].table_schema.columns.clear()
    changed.outputs[name].owner_scope = None
    # Exports need neither provider acquisition nor a readable report body.
    database.rows["final_results"][0]["report"] = "{"
    for method in ("acquire_provider_input", "acquire_provider_candidates"):
        monkeypatch.setattr(
            provider_acquisition, method, Mock(side_effect=AssertionError)
        )
    before = deepcopy(database.rows)
    destination = tmp_path / "export.parquet"
    destination.write_bytes(b"previous export")
    assert (
        reader.export_geoparquet(
            reference, name, str(destination) if string_path else destination
        )
        is None
    )
    actual = gpd.read_parquet(destination)
    assert isinstance(expected, gpd.GeoDataFrame)
    assert_geodataframe_equal(
        actual.sort_values(["location_id", "row_key"]).reset_index(drop=True),
        expected.sort_values(["location_id", "row_key"]).reset_index(drop=True),
        check_dtype=False,
    )
    assert actual.geometry.name == "geometry"
    assert actual.crs is not None
    assert actual.crs.to_epsg() == 4326
    assert actual.attrs == {
        "lyra": {
            "kind": "metric_output",
            "job_id": reference,
            "output_name": name,
            "description": description.outputs[name].model_dump(mode="json"),
        }
    }
    assert database.rows == before
    assert set(tmp_path.iterdir()) == {destination}


def spatial_frame(state: str, *, minimal: bool) -> gpd.GeoDataFrame:
    values = {"label": pd.Series(["first", None], dtype="string")}
    if not minimal:
        values.update(
            {
                "count": pd.Series([2**60 + 1, None], dtype="Int64"),
                "ratio": pd.Series([0.5, None], dtype="Float64"),
                "reviewed": pd.Series([True, None], dtype="boolean"),
            }
        )
    frame = gpd.GeoDataFrame(values, geometry=[Point(1, 2), Point(3, 4)], crs=32614)
    if state == "all-null":
        for name in values:
            frame[name] = frame[name].mask(frame[name].notna())
    if state == "empty":
        frame = frame.iloc[:0]
    return frame


def declaration(frame: gpd.GeoDataFrame) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            name: pa.Column(
                Geometry(crs=32614) if name == "geometry" else series.dtype,
                nullable=True,
                description=str(name),
                checks=pa.Check(
                    lambda values: values.dropna().is_valid.all(), name="valid_geometry"
                )
                if name == "geometry"
                else None,
                metadata={"unit": "m" if name == "ratio" else None},
            )
            for name, series in frame.items()
        },
        strict=True,
    )


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
@pytest.mark.parametrize("minimal", [False, True])
def test_unpartitioned_export(tmp_path: Path, state: str, *, minimal: bool) -> None:
    frame = spatial_frame(state, minimal=minimal)
    path = tmp_path / "store.sqlite"
    _, reference = retained_service(path, declaration(frame), frame)
    with ResultStore.open(path) as reader:
        description = reader.describe(reference)
        destination = tmp_path / "export.parquet"
        reader.export_geoparquet(reference, "rows", destination)
    actual = gpd.read_parquet(destination)
    assert_geodataframe_equal(
        ordered_by_geometry(actual), ordered_by_geometry(frame), check_dtype=False
    )
    assert list(actual.columns) == list(frame.columns)
    assert actual.attrs["lyra"]["description"] == description.outputs[
        "rows"
    ].model_dump(mode="json")
    assert actual.attrs["lyra"]["description"]["owner_scope"] is None
    if not minimal:
        columns = actual.attrs["lyra"]["description"]["table_schema"]["columns"]
        assert next(c["unit"] for c in columns if c["name"] == "ratio") == "meter"


def ordered_by_geometry(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    # These unpartitioned fixtures have distinct geometries and no structural IDs.
    return (
        frame.assign(geometry_order=frame.geometry.to_wkt())
        .sort_values("geometry_order")
        .drop(columns="geometry_order")
        .reset_index(drop=True)
    )


@pytest.mark.parametrize("state", ["populated", "empty"])
@pytest.mark.parametrize("index_kind", ["named", "nondefault", "multi"])
def test_preparation_isolation_and_indexes(
    tmp_path: Path, state: str, index_kind: str
) -> None:
    frame = spatial_frame(state, minimal=True)
    indexes = {
        "named": pd.Index(range(len(frame)), name="producer"),
        "nondefault": pd.Index([7, 9][: len(frame)]),
        "multi": pd.MultiIndex.from_tuples(
            [(7, "a"), (9, "b")][: len(frame)], names=["first", "second"]
        ),
    }
    frame.index = indexes[index_kind]
    frame["incidental"] = "ignored"
    frame.attrs = {"lyra": {"job_id": "forged"}, "nested": {"items": [1]}}
    original = frame.copy(deep=True)
    schema = compile_output(declaration(frame.drop(columns="incidental")))[1]
    metadata: JsonObject = {"nested": {"items": [2]}}
    prepared = geoparquet.prepare_geoparquet(frame, schema, metadata)
    prepared.attrs["lyra"]["nested"]["items"].append(3)
    assert metadata == {"nested": {"items": [2]}}
    destination = tmp_path / "output.parquet"
    geoparquet.write_geoparquet(frame, schema, metadata, destination)
    actual = gpd.read_parquet(destination)
    assert list(actual) == ["label", "geometry"]
    assert actual.attrs == {"lyra": metadata}
    assert_geodataframe_equal(frame, original)
    assert frame.attrs == original.attrs


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("input_export", [False, True])
@pytest.mark.parametrize(
    "failure", ["conversion", "writer", "replace", "cancel", "denied", "close"]
)
def test_atomic_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    *,
    existing: bool,
    input_export: bool,
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    if input_export:
        direct, _ = retained(writer)
        reference = publish_inputs(writer, {"a": direct})
    else:
        reference = publish_result(
            ResultDescriptions(writer), publish_values(writer, ("one",), spatial=True)
        )
    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")
    error = (
        asyncio.CancelledError()
        if failure == "cancel"
        else PermissionError("denied")
        if failure == "denied"
        else ValueError("failure")
    )

    def fail_write(_self: gpd.GeoDataFrame, sink: IO[bytes], **_kwargs: object) -> None:
        sink.write(b"partial")
        raise error

    if failure == "close":
        inject_failure(failure, error, monkeypatch)
    elif failure == "conversion":
        monkeypatch.setattr(geoparquet, "prepare_geoparquet", Mock(side_effect=error))
    elif failure == "replace":
        monkeypatch.setattr(Path, "replace", Mock(side_effect=error))
    elif failure == "denied":
        monkeypatch.setattr(
            geoparquet.tempfile, "NamedTemporaryFile", Mock(side_effect=error)
        )
    else:
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", fail_write)
    reader = ResultStore(writer)
    export = (
        reader.export_location_input_geoparquet
        if input_export
        else reader.export_geoparquet
    )
    with pytest.raises(type(error)) as caught:
        export(reference, "a", destination)
    assert caught.value is error
    assert set(tmp_path.iterdir()) == ({destination} if existing else set())
    if existing:
        assert destination.read_bytes() == b"original"


@pytest.mark.parametrize(
    "failure",
    [
        "reference",
        "name",
        "missing",
        "damage",
        "nonspatial",
        "empty-nonspatial",
        "parent",
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_lookup_and_destination_errors(
    database: Database, tmp_path: Path, failure: str, *, existing: bool
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    retained = publish_values(writer, ("one",), spatial="nonspatial" not in failure)
    reference = publish_result(ResultDescriptions(writer), retained)
    name = "b" if failure == "empty-nonspatial" else "a"
    expected = ValueError
    if failure == "reference":
        reference, expected = "bad", ValidationError
    elif failure == "name":
        name, expected = "missing", ResultNotFoundError
    elif failure == "missing":
        reference, expected = "0" * 32, ResultNotFoundError
    elif failure == "damage":
        database.rows["stored_results"].clear()
        expected = ResultIntegrityError
    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")
    if failure == "parent":
        destination = tmp_path / "missing" / "export.parquet"
        expected = FileNotFoundError
    before = set(tmp_path.iterdir())
    with pytest.raises(expected):
        ResultStore(writer).export_geoparquet(reference, name, destination)
    assert set(tmp_path.iterdir()) == before
    if existing:
        assert (tmp_path / "export.parquet").read_bytes() == b"original"
    else:
        assert not destination.exists()


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("kind", ["direct", "pairs"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("string_path", [False, True])
def test_input_export_round_trip(
    tmp_path: Path, kind: str, *, empty: bool, string_path: bool
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    direct, pairs = retained(writer)
    selection = direct if kind == "direct" else pairs
    if empty:
        selection = selection.model_copy(update={"records": (), "operations": ()})
    reference = publish_inputs(writer, {"selected": selection})
    reader = ResultStore(writer)
    description = reader.describe_result(reference)
    expected = reader.read_location_input(reference, "selected")
    edited = reader.read_location_input(reference, "selected")
    edited["count"] = 999
    edited.attrs["lyra"] = {"job_id": "forged"}
    changed = reader.describe_result(reference)
    changed.location_inputs["selected"].table_schema.columns.clear()
    destination = tmp_path / "export.parquet"
    destination.write_bytes(b"old")
    assert (
        reader.export_location_input_geoparquet(
            reference, "selected", str(destination) if string_path else destination
        )
        is None
    )
    actual = gpd.read_parquet(destination)
    assert_geodataframe_equal(actual, expected, check_dtype=False)
    assert list(actual) == list(expected)
    assert "row_key" not in actual
    assert actual.attrs == {
        "lyra": {
            "kind": "location_input",
            "job_id": reference,
            "input_name": "selected",
            "description": description.location_inputs["selected"].model_dump(
                mode="json"
            ),
        }
    }
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "failure", ["reference", "missing", "name", "damage", "unavailable", "parent"]
)
def test_input_export_lookup_errors(
    database: Database, tmp_path: Path, failure: str, *, existing: bool
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    direct, _ = retained(writer)
    if failure == "unavailable":
        direct = direct.model_copy(
            update={
                "records": (),
                "description": direct.description.model_copy(
                    update={"availability": "not_materialized"}
                ),
            }
        )
    reference = publish_inputs(writer, {"selected": direct})
    name = "selected"
    expected = FileNotFoundError
    if failure == "reference":
        reference, expected = "bad", ValidationError
    elif failure == "missing":
        reference, expected = "0" * 32, ResultNotFoundError
    elif failure == "name":
        name, expected = "unknown", ResultNotFoundError
    elif failure == "damage":
        database.rows[writer.layout.tables[direct.registration.key].table.name].clear()
        expected = ResultIntegrityError
    elif failure == "unavailable":
        expected = LocationInputUnavailableError
    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"old")
    target = (
        tmp_path / "missing" / "export.parquet" if failure == "parent" else destination
    )
    before = set(tmp_path.iterdir())
    with pytest.raises(expected):
        ResultStore(writer).export_location_input_geoparquet(reference, name, target)
    assert set(tmp_path.iterdir()) == before
    if existing:
        assert destination.read_bytes() == b"old"
    else:
        assert not target.exists()
