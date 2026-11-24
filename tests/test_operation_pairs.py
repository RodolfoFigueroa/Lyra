"""Operation completion and immutable publication at an isolated SQL boundary."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.sql.dml import Insert

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.operation_pairs import OperationPairs, operation_registration
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


def header(key: str = "complete", revision: str | None = None) -> Header:
    return Header(
        value_key=key,
        node_id="mapping",
        output_scope="location",
        owner_scope=ProviderScope(provider_id="sources"),
        target_scope=ProviderScope(provider_id="targets", code_revision=revision),
    )


def test_completed_empty_shared_targets_and_ownership(
    database: Database,
    tmp_path: Path,
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    selection = ((header(), ("a", "b", "c")),)
    groups = {"a": ("shared",), "b": ("shared",)}
    assert pairs.lookup(header(), selection[0][1]) is None
    pairs.publish(selection, groups)
    pairs.publish(selection, groups)
    groups["a"] = ("changed", "extra")
    expected = {"a": ("shared",), "b": ("shared",), "c": ()}
    restored = pairs.lookup(header(), ("a", "b", "c"))
    assert restored == expected
    assert restored is not None
    restored.clear()
    pairs.publish(selection, groups)
    assert pairs.lookup(header(), ("a", "b", "c")) == expected
    with pytest.raises(DamagedDataError):
        pairs.publish(((header("new"), ("new",)), (header(), ("a", "b"))), groups)
    assert pairs.lookup(header("new"), ("new",)) is None
    assert pairs.lookup(header(), ("a", "b", "c")) == expected
    assert len(database.rows["stored_results"]) == 1
    table = pairs.store.layout.tables[operation_registration("mapping").key].table
    assert len(database.rows[table.name]) == 2
    assert all(isinstance(row["location_id"], str) for row in database.rows[table.name])
    assert pairs.lookup(header("absent"), ("a",)) is None
    with pytest.raises(DamagedDataError):
        pairs.lookup(header("absent"), ("a",), required=True)
    with pytest.raises(DamagedDataError):
        pairs.lookup(header(revision="changed"), ("a", "b", "c"))


def test_complete_retry_above_sql_parameter_limit(
    database: Database, tmp_path: Path
) -> None:
    def limited(statement: object, parameters: object = None) -> object:
        if isinstance(statement, sa.Select):
            compiled = statement.compile(compile_kwargs={"render_postcompile": True})
            if len(compiled.params) > 8:
                msg = "Too many SQL variables"
                raise ValueError(msg)
        return database.execute(statement, parameters)

    database.connection.execute.side_effect = limited
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    sources = tuple(str(i) for i in range(12))
    selection = ((header(), sources),)
    first: dict[str, tuple[str, ...]] = dict.fromkeys(sources, ())
    first[sources[0]] = ("original",)
    pairs.publish(selection, first)
    pairs.publish(selection, dict.fromkeys(sources, ("changed",)))
    assert pairs.lookup(header(), sources) == first


def test_empty_request_and_partition_computations(
    database: Database,
    tmp_path: Path,
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    pairs.publish((), {})
    assert not database.rows
    pairs.publish(((header(), ()),), {})
    assert pairs.lookup(header(), ()) == {}
    pairs.publish(((header("a"), ("a",)), (header("b"), ("b",))), {"a": ("t",)})
    assert pairs.lookup(header("a"), ("a",)) == {"a": ("t",)}
    assert pairs.lookup(header("b"), ("b",)) == {"b": ()}
    assert len(database.rows["stored_results"]) == 3


@pytest.mark.parametrize(
    "groups", [{"other": ()}, {"a": ("t", "t")}, {"a": (1,)}, {1: ()}, {"a": ["t"]}]
)
def test_invalid_mapping_never_completes(
    database: Database,
    tmp_path: Path,
    groups: dict,
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    with pytest.raises(ValueError, match=r"Invalid operation|validation error"):
        pairs.publish(((header(), ("a",)),), groups)
    assert not database.rows


@pytest.mark.parametrize(
    "damage", ["partition", "source", "duplicate", "registration", "scope"]
)
def test_corruption_is_an_error(
    database: Database,
    tmp_path: Path,
    damage: str,
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    pairs.publish(((header(), ("a", "empty")),), {"a": ("t",)})
    table = pairs.store.layout.tables[operation_registration("mapping").key].table.name
    if damage == "partition":
        database.rows["stored_partitions"].pop()
    elif damage == "source":
        database.rows[table][0]["source_location_id"] = "unsupplied"
    elif damage == "duplicate":
        database.rows[table].append(database.rows[table][0].copy())
    elif damage == "registration":
        database.rows["storage_registrations"][0]["description"] = "broken"
    else:
        database.rows["stored_results"][0]["target_scope"] = None
    with pytest.raises(DamagedDataError):
        pairs.lookup(header(), ("a", "empty"))


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_atomic_batch_failure(
    database: Database,
    tmp_path: Path,
    failure: str,
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "values.sqlite"))
    pairs.store.initialize((operation_registration("mapping"),))
    table = pairs.store.layout.tables[operation_registration("mapping").key].table.name
    pairs.publish(((header("retained"), ("existing",)),), {"existing": ("first",)})
    original = database.execute

    def execute(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == table:
            if failure == "insert":
                message = "insert failure"
                raise RuntimeError(message)
            database.fail_commit = True
        return original(statement, parameters)

    database.connection.execute.side_effect = execute
    with pytest.raises(RuntimeError, match="failure"):
        pairs.publish(
            (
                (header("retained"), ("existing",)),
                (header("a"), ("a",)),
                (header("b"), ("b",)),
            ),
            {"existing": ("other",), "b": ("t",)},
        )
    assert pairs.lookup(header("a"), ("a",)) is None
    assert pairs.lookup(header("b"), ("b",)) is None
    assert pairs.lookup(header("retained"), ("existing",)) == {"existing": ("first",)}
    assert len(database.rows[table]) == 1


@pytest.mark.parametrize("partitioned", [False, True])
@pytest.mark.parametrize(
    "damage",
    ["one", "all", "missing_counts", "negative", "boolean", "string", "extra_source"],
)
def test_missing_pairs_never_become_empty(
    database: Database, tmp_path: Path, damage: str, *, partitioned: bool
) -> None:
    pairs = OperationPairs(SQLiteStore(tmp_path / "pairs.sqlite"))
    selections = (
        ((header("a"), ("a",)), (header("empty"), ("empty",)))
        if partitioned
        else ((header(), ("a", "empty")),)
    )
    pairs.publish(selections, {"a": ("one", "two")})
    table = pairs.store.layout.tables[operation_registration("mapping").key].table.name
    if damage == "one":
        database.rows[table].pop()
    elif damage == "all":
        database.rows[table].clear()
    else:
        counts = {
            "missing_counts": None,
            "negative": '{"pairs":{"a":-1}}',
            "boolean": '{"pairs":{"a":true}}',
            "string": '{"pairs":{"a":"2"}}',
            "extra_source": '{"pairs":{"other":2}}',
        }
        database.rows["stored_results"][0]["output_rows"] = counts[damage]
    selected, sources = selections[0]
    with pytest.raises(DamagedDataError):
        pairs.lookup(selected, sources, required=True)
    with pytest.raises(DamagedDataError):
        pairs.publish(selections, {"a": ("replacement",)})
