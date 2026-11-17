"""Whole-table publication and reconstruction at an isolated SQL boundary."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from sqlalchemy.sql.dml import Insert

from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.sqlite_store import (
    DamagedDataError,
    Header,
    MissingValueError,
    SQLiteStore,
)
from lyra.sdk.storage_schema import describe, prepare_native
from tests.metric_execution_helpers import schema as scoped_schema
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


def header(key: str = "whole") -> Header:
    return Header(value_key=key, node_id="summary", output_scope="unpartitioned")


@pytest.mark.parametrize("kind", ["duplicates", "empty", "null", "boundaries"])
@pytest.mark.parametrize("spatial", [False, True])
def test_native_roundtrip(
    database: Database, tmp_path: Path, kind: str, *, spatial: bool
) -> None:
    columns = {
        name: pa.Column(
            dtype, nullable=True, description="Value", metadata={"unit": None}
        )
        for name, dtype in {
            "s": "string",
            "i": "Int64",
            "f": "Float64",
            "b": "boolean",
        }.items()
    }
    if spatial:
        columns["geometry"] = pa.Column(
            Geometry(crs="EPSG:4326"),
            nullable=True,
            description="Geometry",
            checks=pa.Check(lambda values: values.is_valid.all(), name="valid"),
            metadata={"unit": None},
        )
    schema = pa.DataFrameSchema(columns, strict=True)
    count = 0 if kind == "empty" else 2
    null = kind == "null"
    frame = pd.DataFrame(
        {
            "s": pd.array([None if null else "equal"] * count, dtype="string"),
            "i": pd.array(
                (
                    [-(2**63), 2**63 - 1]
                    if kind == "boundaries"
                    else [None if null else 1] * count
                ),
                dtype="Int64",
            ),
            "f": pd.array(
                (
                    [float.fromhex("0x1.fffffffffffffp+1023"), -1e300]
                    if kind == "boundaries"
                    else [None if null else 1.5] * count
                ),
                dtype="Float64",
            ),
            "b": pd.array([None if null else True] * count, dtype="boolean"),
        }
    )
    if spatial:
        frame = gpd.GeoDataFrame(
            frame,
            geometry=gpd.GeoSeries([None if null else Point(1, 2)] * count, crs=4326),
        )
    layout = describe(schema)
    owned, layout = prepare_native(frame, schema)
    tables = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    tables.publish(
        ((header(), ()),),
        {"a": owned, "b": owned},
        {"a": layout, "b": layout},
        location_inputs={header().value_key: InputBindings()},
    )
    expected = owned.copy(deep=True)
    owned["i"] = 99
    restored = tables.lookup(header(), required=True)
    assert restored is not None
    for value in restored.values():
        pd.testing.assert_frame_equal(value, expected)
    restored["a"]["i"] = 44
    tables.publish(
        ((header(), ()),),
        {"a": owned, "b": owned},
        {"a": layout, "b": layout},
        location_inputs={header().value_key: InputBindings()},
    )
    reopened = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    saved = reopened.lookup(header(), "a", required=True)
    assert saved is not None
    assert set(saved) == {"a"}
    pd.testing.assert_frame_equal(saved["a"], expected)
    assert len(database.rows["stored_results"]) == 1
    assert not database.rows.get("stored_partitions")
    assert database.rows["stored_results"][0]["owner_scope"] is None
    with pytest.raises(MissingValueError):
        reopened.lookup(header(), "unknown")
    assert reopened.lookup(header("absent")) is None
    with pytest.raises(DamagedDataError):
        reopened.lookup(header("absent"), required=True)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_atomic_outputs(
    database: Database, tmp_path: Path, failure: str, *, scoped: bool
) -> None:
    schema = pa.DataFrameSchema(
        {"v": pa.Column("Int64", description="Value", metadata={"unit": None})},
        strict=True,
    )
    layout = describe(schema)
    value, layout = prepare_native(
        pd.DataFrame({"v": pd.array([1], dtype="Int64")}), schema
    )
    h = header()
    partitions = ()
    if scoped:
        schema = scoped_schema()
        layout = describe(schema)
        value = pd.DataFrame(
            {
                "location_id": ["a"],
                "row_key": ["r"],
                "value": pd.array([1], dtype="Int64"),
            }
        )
        h = Header(
            value_key="scoped",
            node_id="summary",
            output_scope="location",
            owner_scope=ProviderScope(provider_id="places"),
        )
        partitions = ("a", "empty")
    tables = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    original = database.execute
    writes = []

    def execute(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name.startswith("data_"):
            writes.append(statement.table.name)
            if len(writes) == 2:
                if failure == "insert":
                    message = "later output failure"
                    raise RuntimeError(message)
                database.fail_commit = True
        return original(statement, parameters)

    database.connection.execute.side_effect = execute
    with pytest.raises(RuntimeError, match="failure"):
        tables.publish(
            ((h, partitions),),
            {"a": value, "b": value},
            {"a": layout, "b": layout},
            location_inputs={h.value_key: InputBindings()},
        )
    assert not database.rows.get("stored_partitions")
    assert len(writes) == 2
    assert not database.rows.get("stored_results")
    assert all(not database.rows.get(name) for name in writes)
    # A failed publication must remain a miss, then permit complete recovery.
    assert tables.lookup(h) is None
    database.fail_commit = False
    database.connection.execute.side_effect = original
    tables.publish(
        ((h, partitions),),
        {"a": value, "b": value},
        {"a": layout, "b": layout},
        location_inputs={h.value_key: InputBindings()},
    )
    restored = tables.lookup(h, required=True)
    assert restored is not None
    for name in ("a", "b"):
        pd.testing.assert_frame_equal(
            restored[name],
            value.astype(
                {"location_id": "string", "row_key": "string"} if scoped else {}
            ),
        )


@pytest.mark.parametrize("names", [(), ("a", "extra")])
def test_exact_output_names(
    database: Database, tmp_path: Path, names: tuple[str, ...]
) -> None:
    layout = describe(pa.DataFrameSchema({}, strict=True))
    tables = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    with pytest.raises(ValueError, match="exactly"):
        tables.publish(
            ((header(), ()),),
            {name: pd.DataFrame() for name in names},
            {"a": layout},
            location_inputs={header().value_key: InputBindings()},
        )
    assert not database.rows


@pytest.mark.parametrize("damage", ["rows", "description", "null", "scope"])
def test_damaged_required_data(database: Database, tmp_path: Path, damage: str) -> None:
    schema = pa.DataFrameSchema(
        {"v": pa.Column("Int64", description="Value", metadata={"unit": None})},
        strict=True,
    )
    layout = describe(schema)
    tables = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    tables.publish(
        ((header(), ()),),
        {"a": pd.DataFrame({"v": [1]})},
        {"a": layout},
        location_inputs={header().value_key: InputBindings()},
    )
    table = next(iter(tables.store.layout.tables.values())).table.name
    if damage == "rows":
        database.rows[table].clear()
    elif damage == "description":
        database.rows["stored_results"][-1]["output_rows"] = None
    elif damage == "null":
        database.rows[table][0]["v"] = None
    else:
        database.rows["stored_results"][0]["node_id"] = "wrong"
    with pytest.raises(DamagedDataError):
        tables.lookup(header())


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_scoped_groups(
    database: Database, tmp_path: Path, *, spatial: bool, empty: bool
) -> None:
    declaration = scoped_schema(spatial=spatial)
    if spatial:
        declaration = declaration.update_column("geometry", nullable=True)
    layout = describe(declaration)
    frame = pd.DataFrame(
        {
            "location_id": pd.array([] if empty else ["a", "b"], dtype="string"),
            "row_key": pd.array([] if empty else ["same", "same"], dtype="string"),
            "value": pd.array([] if empty else [None, None], dtype="Int64"),
        }
    )
    if spatial:
        frame = gpd.GeoDataFrame(
            frame, geometry=gpd.GeoSeries([] if empty else [None, None], crs=4326)
        )
    tables = MetricTables(SQLiteStore(tmp_path / "values.sqlite"))
    headers = [
        Header(
            value_key=str(i),
            node_id="scoped",
            output_scope="location",
            owner_scope=ProviderScope(
                provider_id="places" if i < 2 else "districts",
                code_revision=str(i),
                origins=frozenset({Origin(id="survey", revision=str(i))}),
            ),
        )
        for i in range(3)
    ]
    for h in headers:
        tables.publish(
            ((h, ("a", "b", "empty")),),
            {"one": frame, "two": frame},
            {"one": layout, "two": layout},
            location_inputs={h.value_key: InputBindings()},
        )
        tables.publish(
            ((h, ("a", "b", "empty")),),
            {"one": frame, "two": frame},
            {"one": layout, "two": layout},
            location_inputs={h.value_key: InputBindings()},
        )
        selected = tables.lookup(h, partitions=("a", "empty"), required=True)
        assert selected is not None
        assert set(selected) == {"one", "two"}
        assert selected["one"].location_id.tolist() == ([] if empty else ["a"])
        assert str(selected["one"].value.dtype) == "Int64"
        blank = tables.lookup(h, partitions=("empty",), required=True)
        assert blank is not None
        assert blank["one"].empty
        if spatial:
            assert blank["one"].crs.to_epsg() == 4326
    assert len(database.rows["stored_results"]) == 3
    assert len(database.rows["stored_partitions"]) == 9
    with pytest.raises(DamagedDataError):
        tables.lookup(
            headers[0].model_copy(update={"owner_scope": headers[1].owner_scope})
        )
    with pytest.raises(DamagedDataError):
        tables.lookup(headers[0], partitions=("absent",))
    if not empty:
        with pytest.raises(ValueError, match="primary"):
            tables.publish(
                ((headers[0].model_copy(update={"value_key": "bad"}), ("a",)),),
                {"one": frame, "two": frame},
                {"one": layout, "two": layout},
                location_inputs={
                    headers[0]
                    .model_copy(update={"value_key": "bad"})
                    .value_key: InputBindings()
                },
            )
    database.rows["stored_partitions"].pop(0)
    with pytest.raises(DamagedDataError):
        tables.lookup(headers[0])
