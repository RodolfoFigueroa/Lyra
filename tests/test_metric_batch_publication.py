"""Invocation atomicity preserves independently reusable metric partitions."""

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.sql.dml import Insert

from lyra.contracts.errors import ExecutionPersistenceError
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from lyra.sdk.storage_schema import describe
from test_support.retained_input_workloads import retained_inputs, retained_workload
from tests.location_input_helpers import retained
from tests.metric_execution_helpers import schema
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


def headers(*keys: str) -> tuple[tuple[Header, tuple[str, ...]], ...]:
    return tuple(
        (
            Header(
                value_key=k,
                node_id="metric",
                output_scope="location",
                owner_scope=ProviderScope(provider_id="places"),
            ),
            (k,),
        )
        for k in keys
    )


def publish(
    tables: MetricTables, keys: tuple[str, ...], bindings: InputBindings
) -> None:
    frame = pd.DataFrame(
        {
            "location_id": list(keys),
            "row_key": ["r"] * len(keys),
            "value": pd.array(range(len(keys)), dtype="Int64"),
        }
    )
    tables.publish(
        headers(*keys),
        {"a": frame, "b": frame.copy()},
        {"a": describe(schema()), "b": describe(schema())},
        location_inputs=dict.fromkeys(keys, bindings),
    )


@pytest.mark.parametrize("fault", ["output", "binding", "commit"])
def test_batch_rollback_and_retry(
    database: Database, tmp_path: Path, fault: str
) -> None:
    tables = MetricTables(SQLiteStore(tmp_path / "store"))
    selection, _ = retained(tables.store)
    reference = LocationInputs(tables.store).publish(selection)
    bindings = InputBindings(names=("shared",), selections={"shared": (reference,)})
    publish(tables, ("a", "b"), bindings)
    execute = database.execute

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert):
            records = parameters if isinstance(parameters, list) else [parameters]
            if any(
                isinstance(row, dict) and row.get("value_key") == "d" for row in records
            ):
                if fault == "commit":
                    database.fail_commit = True
                elif (
                    fault == "binding" and statement.table.name == "metric_inputs"
                ) or (fault == "output" and statement.table.name.startswith("data_")):
                    msg = "batch failure"
                    raise RuntimeError(msg)
        return execute(statement, parameters)

    database.connection.execute.side_effect = inject
    with pytest.raises(RuntimeError, match="failure"):
        publish(tables, ("b", "c", "d"), bindings)
    database.connection.execute.side_effect = execute
    database.fail_commit = False
    for h, _ in headers("a", "b"):
        assert tables.lookup(h, required=True) is not None
    for h, _ in headers("c", "d"):
        assert tables.lookup(h) is None
    assert {row["value_key"] for row in database.rows["metric_inputs"]} == {"a", "b"}
    publish(tables, ("b", "c", "d"), bindings)
    assert {row["value_key"] for row in database.rows["metric_inputs"]} == {
        "a",
        "b",
        "c",
        "d",
    }
    for h, _ in headers("a", "b", "c", "d"):
        saved = tables.lookup(h, required=True)
        assert saved is not None
        assert saved["a"].location_id.tolist() == [h.value_key]
    assert len(database.rows["input_selections"]) == 1
    # A later publication must revalidate shared inputs against persisted state.
    data = tables.store.layout.tables[selection.registration.key].table.name
    database.rows[data].clear()
    with pytest.raises(DamagedDataError):
        publish(tables, ("e", "f"), bindings)
    assert tables.lookup(headers("e")[0][0]) is None


@pytest.mark.parametrize("fault", ["duplicate", "overlap", "scope", "inventory"])
def test_invalid_batch_is_not_published(
    database: Database, tmp_path: Path, fault: str
) -> None:
    tables = MetricTables(SQLiteStore(tmp_path / "store"))
    selections = headers("a", "b")
    bindings = {"a": InputBindings(), "b": InputBindings()}
    if fault == "duplicate":
        selections = (selections[0], selections[0])
    elif fault == "overlap":
        selections = (selections[0], (selections[1][0], ("a",)))
    elif fault == "scope":
        selections = (
            selections[0],
            (selections[1][0].model_copy(update={"node_id": "other"}), ("b",)),
        )
    else:
        bindings.pop("b")
    frame = pd.DataFrame(
        {"location_id": [], "row_key": [], "value": pd.array([], dtype="Int64")}
    )
    with pytest.raises(ValueError, match=r"inventory|overlap|scopes"):
        tables.publish(
            selections,
            {"a": frame},
            {"a": describe(schema())},
            location_inputs=bindings,
        )
    assert not database.rows


@pytest.mark.usefixtures("database")
def test_conflicting_bindings_abort_mixed_retry(tmp_path: Path) -> None:
    tables = MetricTables(SQLiteStore(tmp_path / "store"))
    direct, _ = retained(tables.store)
    reference = LocationInputs(tables.store).publish(direct)
    bindings = InputBindings(names=("shared",), selections={"shared": (reference,)})
    assert tables.lookup(headers("a")[0][0]) is None
    publish(tables, ("a",), bindings)
    with pytest.raises(DamagedDataError, match="bindings differ"):
        publish(tables, ("new", "a"), InputBindings())
    assert tables.lookup(headers("new")[0][0]) is None
    saved = tables.lookup(headers("a")[0][0], required=True)
    assert saved is not None
    assert saved["a"].value.tolist() == [0]


@pytest.mark.parametrize(
    "damage", ["binding", "registration", "inventory", "partition", "rows"]
)
def test_existing_damage_aborts_pending_batch(
    database: Database, tmp_path: Path, damage: str
) -> None:
    tables = MetricTables(SQLiteStore(tmp_path / "store"))
    publish(tables, ("a",), InputBindings())
    if damage == "binding":
        database.rows["metric_inputs"].clear()
    elif damage == "registration":
        database.rows["storage_registrations"].pop()
    elif damage == "inventory":
        database.rows["stored_results"][0]["output_rows"] = '{"a":{"a":1}}'
    elif damage == "partition":
        database.rows["stored_partitions"].clear()
    else:
        data = next(iter(tables.store.layout.tables.values()))
        database.rows[data.table.name].clear()
    with pytest.raises(DamagedDataError):
        publish(tables, ("b", "a"), InputBindings())
    assert tables.lookup(headers("b")[0][0]) is None


def test_shared_restrictions_remain_distinct(
    database: Database, tmp_path: Path
) -> None:
    tables = MetricTables(SQLiteStore(tmp_path / "store"))
    direct, correspondence = retained(tables.store)
    inputs = LocationInputs(tables.store)
    first = inputs.publish(direct)
    second = inputs.publish(correspondence)
    bindings = InputBindings(
        names=("provider", "pairs"),
        selections={"provider": (first,), "pairs": (second,)},
    )
    publish(tables, ("a", "b"), bindings)
    with tables.store.engine.connect() as connection:
        assert inputs.frame(connection, (first,)).location_id.tolist() == ["a", "b"]
        pairs = inputs.frame(connection, (second,))
        assert set(zip(pairs.source_location_id, pairs.location_id, strict=True)) == {
            ("A", "a"),
            ("B", "a"),
        }
    assert len(database.rows["input_selections"]) == 2


def test_execution_retry_reuses_only_committed_partitions(
    database: Database, tmp_path: Path
) -> None:

    work = retained_workload(derived=False)
    runner = LocalRunner.from_definitions(
        {"bench": work.plugin}, result_store_path=tmp_path / "store"
    )
    runner.run("root", inputs=retained_inputs(2, 3))
    execute = database.execute
    inserted = []

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == "metric_inputs":
            inserted.append(parameters)
            if len(inserted) == 2:
                msg = "later partition failure"
                raise RuntimeError(msg)
        return execute(statement, parameters)

    database.connection.execute.side_effect = inject
    with pytest.raises(ExecutionPersistenceError) as caught:
        runner.run("root", inputs=retained_inputs(4, 3))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "later partition failure"
    database.connection.execute.side_effect = execute
    work.calls.clear()
    runner.run("root", inputs=retained_inputs(4, 3))
    assert [ids for name, ids in work.calls if name == "root"] == [
        frozenset({"s2", "s3"})
    ]
    work.calls.clear()
    runner.run("root", inputs=retained_inputs(4, 3))
    assert work.calls == []
