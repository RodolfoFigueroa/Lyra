"""SQL-bounded retained traversal and resource lifecycles without live services."""

from contextlib import closing, nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.output_selection import OutputSelection
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
)
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from tests.retained_table_helpers import mixed_result
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.parametrize("batch_size", [1, 2, 3, 10])
def test_selected_batches_use_bounded_sql(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch_size: int
) -> None:
    descriptions, reference = mixed_result(SQLiteStore(tmp_path / "results.sqlite"))
    before = deepcopy(database.rows)
    monkeypatch.setattr(
        OutputSelection, "frame", Mock(side_effect=AssertionError("No full frames"))
    )
    queries = []
    cursors = []

    def execute(statement: sa.Select, parameters: object = None) -> object:
        result = database.execute(statement, parameters)
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        if source.name.startswith("data_"):
            queries.append(statement)
            cursors.append(result)
        return result

    database.connection.execute.side_effect = execute
    with descriptions.select(reference, "a") as selected:
        # Validation projects only SQL aggregates, never all row values.
        assert queries
        assert all(
            "count(" in str(query.compile(dialect=sqlite.dialect()))
            for query in queries
        )
        queries.clear()
        batches = list(selected.batches(batch_size))
        assert selected.owner_scope == ProviderScope(
            provider_id="places", code_revision="1"
        )
        assert [column.name for column in selected.registration.columns] == [
            "location_id",
            "row_key",
            "value",
        ]
    assert all(0 < len(batch) <= batch_size for batch in batches)
    assert [row for batch in batches for row in batch] == [
        ("same", "a", 10),
        ("same", "z", 30),
        ("next", "r", 2),
    ]
    assert len(queries) == 2
    for cursor in cursors[-2:]:
        cursor.mappings.return_value.partitions.assert_called_once_with(batch_size)
    for query in queries:
        compiled = query.compile(dialect=sqlite.dialect())
        sql = str(compiled).upper()
        assert "ORDER BY" in sql
        assert "WHERE" in sql
        assert "LOCATION_ID IN" in sql
        assert "DISTINCT" not in sql
    assert all(cursor.close.called for cursor in cursors)
    database.connection.__exit__.assert_called()
    assert database.rows == before


@pytest.mark.parametrize("exit_kind", ["early", "error", "exhausted", "fetch-error"])
def test_batch_resources_close(
    database: Database, tmp_path: Path, exit_kind: str
) -> None:
    descriptions, reference = mixed_result(SQLiteStore(tmp_path / "results.sqlite"))
    database.connection.reset_mock()
    cursors = []

    def execute(statement: sa.Select, parameters: object = None) -> object:
        cursor = database.execute(statement, parameters)
        if "ORDER BY" in str(statement):
            cursors.append(cursor)
            if exit_kind == "fetch-error":
                cursor.mappings.side_effect = RuntimeError("fetch failed")
        return cursor

    database.connection.execute.side_effect = execute
    expectation = (
        pytest.raises(RuntimeError, match="failed")
        if exit_kind in {"error", "fetch-error"}
        else nullcontext()
    )
    with expectation, descriptions.select(reference, "a") as selected:
        if exit_kind == "exhausted":
            assert len(list(selected.batches(1))) == 3
        else:
            with closing(selected.batches(1)) as batches:
                assert len(next(batches)) == 1
                assert not cursors[-1].close.called
                if exit_kind == "error":
                    msg = "consumer failed"
                    raise RuntimeError(msg)
    assert cursors
    assert all(cursor.close.called for cursor in cursors)
    database.connection.__exit__.assert_called()


@pytest.mark.parametrize("damage", ["rows", "duplicate", "partition", "header"])
def test_checks_precede_value_batches(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    descriptions, reference = mixed_result(store)
    data = next(iter(store.layout.tables.values()))
    rows = database.rows[data.table.name]
    selected = next(
        row
        for row in rows
        if row[data.value_column] == "cached" and row["row_key"] == "a"
    )
    if damage == "rows":
        rows.remove(selected)
    elif damage == "duplicate":
        selected["row_key"] = "z"
    elif damage == "partition":
        database.rows["stored_partitions"] = [
            row
            for row in database.rows["stored_partitions"]
            if row["location_id"] != "empty"
        ]
    else:
        database.rows["stored_results"] = [
            row
            for row in database.rows["stored_results"]
            if row["value_key"] != "fresh"
        ]
    with pytest.raises(DamagedDataError), descriptions.select(reference, "a"):
        pytest.fail("Integrity must be checked before a batch consumer starts")


@pytest.mark.parametrize("value_key", ["other-provider", "other-code", "other-origin"])
def test_incompatible_retained_scope_is_rejected(
    database: Database, tmp_path: Path, value_key: str
) -> None:
    descriptions, reference = mixed_result(SQLiteStore(tmp_path / "results.sqlite"))
    row = database.rows["final_results"][0]
    description = ResultDescription.model_validate_json(row["description"])
    description.outputs["a"] = description.outputs["a"].model_copy(
        update={
            "computations": (
                ComputationSelection(value_key=value_key, groups=("same",)),
            ),
        }
    )
    row["description"] = description.model_dump_json()
    with pytest.raises(DamagedDataError), descriptions.select(reference, "a"):
        pytest.fail("Bare location and row IDs cannot replace concrete ownership")


def test_unselected_groups_are_not_audited(database: Database, tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    descriptions, reference = mixed_result(store)
    data = next(iter(store.layout.tables.values()))
    for row in database.rows[data.table.name]:
        if row[data.value_column] == "fresh" and row["location_id"] == "same":
            row["row_key"] = None
    with descriptions.select(reference, "a") as selected:
        assert [row for batch in selected.batches(1) for row in batch] == [
            ("same", "a", 10),
            ("same", "z", 30),
            ("next", "r", 2),
        ]


def test_batches_cross_location_boundaries(database: Database, tmp_path: Path) -> None:
    descriptions, reference = mixed_result(SQLiteStore(tmp_path / "results.sqlite"))
    row = database.rows["final_results"][0]
    description = ResultDescription.model_validate_json(row["description"])
    description.outputs["a"] = description.outputs["a"].model_copy(
        update={
            "computations": (ComputationSelection(value_key="cached"),),
        }
    )
    row["description"] = description.model_dump_json()
    with descriptions.select(reference, "a") as selected:
        assert [row for batch in selected.batches(1) for row in batch] == [
            ("next", "r", 20),
            ("same", "a", 10),
            ("same", "z", 30),
        ]


@pytest.mark.parametrize("size", [0, -1, True])
def test_invalid_batch_bounds(database: Database, tmp_path: Path, size: int) -> None:
    descriptions, reference = mixed_result(SQLiteStore(tmp_path / "results.sqlite"))
    with (
        descriptions.select(reference, "a") as selected,
        pytest.raises(ValueError, match="positive integer"),
    ):
        next(selected.batches(size))
    database.connection.__exit__.assert_called()
