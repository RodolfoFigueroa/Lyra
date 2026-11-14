"""Final selections are durable independently of jobs and cache reuse."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy.sql.dml import Insert

from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescriptions,
)
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.parametrize("keys", [(), ("one",), ("one", "two")])
@pytest.mark.parametrize("spatial", [False, True])
def test_reopen_assembly(
    database: Database, tmp_path: Path, keys: tuple[str, ...], *, spatial: bool
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, keys, spatial=spatial)
    values = deepcopy(database.rows)
    results = ResultDescriptions(store)
    first = results.publish(description)
    second = results.publish(description)
    assert first != second
    assert {
        name: rows for name, rows in database.rows.items() if name != "final_results"
    } == values
    reopened = ResultDescriptions(SQLiteStore(store.path))
    restored, frames = reopened.read(first)
    assert restored == description
    assert frames["a"].location_id.tolist() == list(keys)
    assert frames["b"].empty
    if spatial:
        assert frames["a"].crs.to_epsg() == 4326
        assert frames["b"].crs.to_epsg() == 4326
    assert str(frames["b"].value.dtype) == "Int64"
    restored.parameters.clear()
    frames["a"]["value"] = 99
    again, original = reopened.read(first)
    assert again == description
    assert original["a"].value.tolist() == [1] * len(keys)
    assert reopened.read(second)[0] == description


@pytest.mark.parametrize("failure", ["insert", "commit", "missing", "group"])
def test_failed_description_keeps_values(
    database: Database, tmp_path: Path, failure: str
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, ("one",))
    if failure in {"missing", "group"}:
        description.outputs["b"] = description.outputs["b"].model_copy(
            update={
                "computations": (
                    ComputationSelection(
                        value_key="absent" if failure == "missing" else "one",
                        groups=("absent",),
                    ),
                )
            }
        )
    execute = database.execute

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == "final_results":
            if failure == "insert":
                msg = "publication failure"
                raise RuntimeError(msg)
            database.fail_commit = failure == "commit"
        return execute(statement, parameters)

    database.connection.execute.side_effect = inject
    with pytest.raises((RuntimeError, ValueError, DamagedDataError)):
        ResultDescriptions(store).publish(description)
    assert not database.rows.get("final_results")
    assert len(database.rows["stored_results"]) == 1


@pytest.mark.parametrize("damage", ["missing", "extra"])
def test_incomplete_description_is_not_published(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "values.sqlite")
    description = publish_values(store, ())
    if damage == "missing":
        del description.outputs["b"]
    else:
        description.outputs["extra"] = description.outputs["b"]
    with pytest.raises(DamagedDataError):
        ResultDescriptions(store).publish(description)
    assert not database.rows.get("final_results")
