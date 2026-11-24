"""Retained selections exercise real reconstruction with isolated SQL acquisition."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pyarrow as pa
import pytest
from pydantic import ValidationError
from sqlalchemy.sql.dml import Insert

from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.location_inputs import (
    InputBindings,
    LocationInputs,
)
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.operation_pairs import operation_registration
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.result_descriptions import ComputationSelection, ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from test_support.publication import publish_result
from tests.acquisition_helpers import acquisition_fixture
from tests.location_input_helpers import publish_inputs, retained
from tests.provider_schema_helpers import frame
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.usefixtures("database")
def test_native_inputs_survive_closure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, correspondence = retained(store)
    reference = publish_inputs(store, {"blocks": direct, "nearby": correspondence})
    store.close()
    store.path.touch()
    with ResultStore.open(store.path) as reader:
        blocks = reader.read_location_input(reference, "blocks")
        pairs = reader.read_location_input(reference, "nearby")
        declared = (
            reader.describe_result(reference).location_inputs["nearby"].table_schema
        )
        source = next(
            column for column in declared.columns if column.name == "source_location_id"
        )
        with pa.ipc.open_stream(
            b"".join(reader.stream_location_input(reference, "nearby"))
        ) as stream:
            field = stream.schema.field(source.name)
            assert field.metadata[b"description"] == source.description.encode()
            assert field.nullable == source.nullable
        assert blocks.location_id.tolist() == ["a", "b"]
        assert str(blocks["count"].dtype) == "Int64"
        assert blocks["count"].isna().tolist() == [False, True]
        assert pairs[["source_location_id", "location_id"]].values.tolist() == [
            ["A", "a"],
            ["B", "a"],
        ]
        assert pairs.geometry.name == "geometry"
        assert pairs.crs is not None
        assert pairs.crs.to_epsg() == 4326
        assert "row_key" not in pairs
        pairs.loc[:, "count"] = 99
        assert reader.read_location_input(reference, "nearby")["count"].tolist() == [
            1,
            1,
        ]
        assert set(reader.read_result(reference).tables) == {"a", "b"}
    assert blocks.geometry.iloc[0].x == 0


@pytest.mark.usefixtures("database")
def test_empty_unavailable_and_unknown(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, correspondence = retained(store)
    empty = direct.model_copy(update={"records": ()})
    unavailable = empty.model_copy(
        update={
            "description": empty.description.model_copy(
                update={"availability": "not_materialized"}
            )
        }
    )
    zero = correspondence.model_copy(
        update={
            "operations": (
                correspondence.operations[0].model_copy(update={"sources": ("empty",)}),
            )
        }
    )
    reference = publish_inputs(
        store, {"empty": empty, "zero": zero, "skipped": unavailable}
    )
    reader = ResultStore(store)
    for name in ("empty", "zero"):
        result = reader.read_location_input(reference, name)
        assert result.empty
        assert str(result["count"].dtype) == "Int64"
        assert result.crs is not None
        assert result.crs.to_epsg() == 4326
    descriptions = reader.describe_result(reference).location_inputs
    for name in ("empty", "zero", "skipped"):
        assert descriptions[name].provider_scope.description == "Target records"
    source = descriptions["zero"].source_scope
    assert source is not None
    assert source.description == "Source records"
    with pytest.raises(LocationInputUnavailableError):
        reader.read_location_input(reference, "skipped")
    for name in ("metric_dependency", "unknown"):
        with pytest.raises(ResultNotFoundError) as caught:
            reader.read_location_input(reference, name)
        assert caught.value.input_name == name
        assert caught.value.output_name is None


@pytest.mark.parametrize(
    "damage", ["records", "pairs", "completion", "selection", "schema", "inventory"]
)
def test_damage_never_becomes_empty(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, correspondence = retained(store)
    reference = publish_inputs(store, {"blocks": direct, "nearby": correspondence})
    if damage == "records":
        database.rows[store.layout.tables[direct.registration.key].table.name].clear()
    elif damage == "pairs":
        database.rows[
            store.layout.tables[operation_registration("nearby").key].table.name
        ].clear()
    elif damage == "completion":
        database.rows[store.layout.results.name] = [
            row
            for row in database.rows[store.layout.results.name]
            if row["value_key"] != "operation"
        ]
    elif damage == "selection":
        database.rows[store.layout.input_selections.name].clear()
    elif damage == "schema":
        database.rows[store.layout.registrations.name].clear()
    else:
        row = database.rows[store.layout.final_results.name][0]
        desc = json.loads(row["description"])
        desc["location_inputs"]["selections"].pop("nearby")
        row["description"] = json.dumps(desc)
    with pytest.raises(ResultIntegrityError) as caught:
        ResultStore(SQLiteStore(store.path)).read_location_input(reference, "nearby")
    assert caught.value.input_name == "nearby"


@pytest.mark.usefixtures("database")
def test_provider_scope_isolation(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, _ = retained(store)
    prepared, _ = acquisition_fixture(Mock())
    newer = replace(
        prepared.provider,
        scope=ProviderScope(
            provider_id=direct.description.provider_scope.provider_id,
            code_revision="new",
        ),
    )
    values = newer.schema.validate(frame())
    values.loc[:, "count"] = 7
    ProviderRecords(store).publish(newer, values)
    changed = direct.model_copy(
        update={
            "description": direct.description.model_copy(
                update={"provider_scope": newer.scope}
            )
        }
    )
    reference = publish_inputs(store, {"old": direct, "new": changed})
    reader = ResultStore(store)
    assert reader.read_location_input(reference, "old")["count"].iloc[0] == 1
    assert reader.read_location_input(reference, "new")["count"].tolist() == [7, 7]
    for name, counts in (("old", [1, None]), ("new", [7, 7])):
        destination = tmp_path / f"{name}.parquet"
        reader.export_location_input_geoparquet(reference, name, destination)
        exported = gpd.read_parquet(destination)
        assert exported.location_id.tolist() == ["a", "b"]
        assert exported["count"].iloc[0] == counts[0]
        assert exported.attrs["lyra"]["description"] == reader.describe_result(
            reference
        ).location_inputs[name].model_dump(mode="json")


def test_shared_groups_and_atomic_metric_bindings(
    database: Database, tmp_path: Path
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, _ = retained(store)
    inputs = LocationInputs(store)
    shared = inputs.publish(direct)
    assert inputs.publish(direct) == shared
    subset = inputs.publish(direct.model_copy(update={"records": ("a",)}))
    bindings = InputBindings(
        names=("shared", "primary"),
        selections={"shared": (shared,)},
        groups={"one": {"primary": (subset,)}, "two": {"primary": (shared,)}},
    )
    description = publish_values(store, ("template",))
    outputs = MetricTables(store).lookup(
        Header(
            value_key="template",
            node_id="metric",
            output_scope="location",
            owner_scope=description.outputs["a"].owner_scope,
        ),
        required=True,
    )
    assert outputs is not None
    layouts = {
        name: store.layout.descriptions[output.registration_key].storage_description()
        for name, output in description.outputs.items()
    }
    header = Header(
        value_key="bound",
        node_id="metric",
        output_scope="location",
        owner_scope=description.outputs["a"].owner_scope,
    )
    execute = database.execute

    def fail_binding(statement: object, parameters: object = None) -> object:
        if (
            isinstance(statement, Insert)
            and statement.table.name == store.layout.metric_inputs.name
        ):
            msg = "binding failure"
            raise RuntimeError(msg)
        return execute(statement, parameters)

    database.connection.execute.side_effect = fail_binding
    with pytest.raises(RuntimeError, match="binding failure"):
        MetricTables(store).publish(
            ((header, ("template", "one", "two")),),
            outputs,
            layouts,
            location_inputs={header.value_key: bindings},
        )
    database.connection.execute.side_effect = execute
    assert MetricTables(store).lookup(header) is None
    MetricTables(store).publish(
        ((header, ("template", "one", "two")),),
        outputs,
        layouts,
        location_inputs={header.value_key: bindings},
    )
    description = description.model_copy(
        update={
            "input_computations": (
                ComputationSelection(value_key="bound", groups=("one",)),
            )
        }
    )
    reference = publish_result(ResultDescriptions(store), description)
    reader = ResultStore(store)
    assert reader.read_location_input(reference, "shared").location_id.tolist() == [
        "a",
        "b",
    ]
    assert reader.read_location_input(reference, "primary").location_id.tolist() == [
        "a"
    ]
    assert len(database.rows[store.layout.input_selections.name]) == 2
    _assert_completion_required(
        reader,
        reference,
        next(
            row
            for row in database.rows[store.layout.results.name]
            if row["value_key"] == "bound"
        ),
    )
    database.rows[store.layout.metric_inputs.name].clear()
    with pytest.raises(DamagedDataError):
        MetricTables(store).lookup(header, required=True)
    with pytest.raises(ResultIntegrityError):
        reader.read_location_input(reference, "shared")


@pytest.mark.parametrize("fault", ["missing_selection", "commit"])
def test_final_publication_failure_is_atomic(
    database: Database, tmp_path: Path, fault: str
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, _ = retained(store)
    key = LocationInputs(store).publish(direct)
    description = publish_values(store, ("one",))
    description = description.model_copy(
        update={
            "location_inputs": InputBindings(
                names=("blocks",), selections={"blocks": (key,)}
            )
        }
    )
    if fault == "missing_selection":
        database.rows[store.layout.input_selections.name].clear()
    else:
        database.fail_commit = True
    with pytest.raises((DamagedDataError, RuntimeError)):
        publish_result(ResultDescriptions(store), description)
    assert not database.rows.get(store.layout.final_results.name)
    assert database.rows[store.layout.results.name]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("invalid", [None, 123, "", " "])
def test_invalid_lookup_names_and_group_inventory(tmp_path: Path, invalid: str) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, _ = retained(store)
    reference = publish_inputs(store, {"blocks": direct})
    expected = ResultNotFoundError if isinstance(invalid, str) else ValidationError
    with pytest.raises(expected):
        ResultStore(store).read_location_input(reference, invalid)
    bindings = InputBindings(
        names=("blocks",), groups={"A": {"blocks": ("selection",)}}
    )
    with pytest.raises(DamagedDataError):
        bindings.selected(("B",))


def _assert_completion_required(
    reader: ResultStore, reference: str, completion: dict
) -> None:
    counts = completion["output_rows"]
    completion["output_rows"] = None
    with pytest.raises(ResultIntegrityError):
        reader.read_location_input(reference, "shared")
    completion["output_rows"] = counts


def test_reuse_rejects_missing_operation_relationship(
    database: Database, tmp_path: Path
) -> None:
    store = SQLiteStore(tmp_path / "relationship.sqlite")
    _, correspondence = retained(store)
    inputs = LocationInputs(store)
    selection = inputs.publish(correspondence)
    publish_values(store, ("template",))
    bindings = InputBindings(names=("places",), selections={"places": (selection,)})
    for row in database.rows[store.layout.metric_inputs.name]:
        if row["value_key"] == "template":
            row["bindings"] = bindings.model_dump_json()
    database.rows[store.layout.results.name][:] = [
        row
        for row in database.rows[store.layout.results.name]
        if row["value_key"] != "operation"
    ]
    with store.engine.connect() as connection, pytest.raises(DamagedDataError):
        inputs.bindings(connection, "template")


@pytest.mark.usefixtures("database")
def test_retained_metadata_is_detached(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "ownership.sqlite")
    direct, _ = retained(store)
    reference = publish_inputs(store, {"blocks": direct, "shared": direct})
    reader = ResultStore(store)
    first = reader.describe(reference)
    first.location_inputs["blocks"].table_schema.columns.clear()
    assert first.location_inputs["shared"] == direct.description
    assert reader.describe(reference).location_inputs["blocks"] == direct.description
    inputs = LocationInputs(store)
    with store.engine.connect() as connection:
        selection = inputs.load(connection, direct.key)
        assert selection.registration.table_schema is not None
        selection.registration.table_schema.columns.clear()
        assert inputs.load(connection, direct.key) == direct


@pytest.mark.parametrize("damage", ["missing", "address", "header", "groups"])
def test_later_traversal_detects_corruption(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "later.sqlite")
    _, correspondence = retained(store)
    inputs = LocationInputs(store)
    key = inputs.publish(correspondence)
    publish_values(store, ("template",))
    bindings = InputBindings(names=("places",), selections={"places": (key,)})
    for row in database.rows[store.layout.metric_inputs.name]:
        if row["value_key"] == "template":
            row["bindings"] = bindings.model_dump_json()
    with store.engine.connect() as connection:
        first = inputs.bindings(connection, "template")
        first.selections.clear()
        assert inputs.bindings(connection, "template") == bindings
        if damage == "missing":
            database.rows[store.layout.input_selections.name].clear()
        elif damage == "address":
            row = database.rows[store.layout.input_selections.name][0]
            payload = json.loads(row["description"])
            payload["operations"][0]["targets"] = ["b"]
            row["description"] = json.dumps(payload)
        else:
            row = next(
                row
                for row in database.rows[store.layout.results.name]
                if row["value_key"] == "operation"
            )
            if damage == "header":
                row["node_id"] = "another"
            else:
                row["output_rows"] = '{"pairs": {}}'
        with pytest.raises(DamagedDataError):
            inputs.bindings(connection, "template")
