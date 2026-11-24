"""SQL pagination invariants at the isolated retained-storage boundary."""

import base64
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite

from lyra.contracts.errors import (
    InvalidPaginationError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.output_selection import OutputSelection
from lyra.runtime.pagination import RowCandidate
from lyra.runtime.result_descriptions import ComputationSelection, ResultDescription
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from tests.retained_table_helpers import mixed_result, retained_service
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def page(
    reader: ResultStore,
    reference: str,
    output: str = "a",
    limit: int = 100,
    cursor: str | None = None,
) -> list[RowCandidate]:
    with reader.page_candidates(reference, output, limit, cursor) as selected:
        return list(selected.rows)


def select_computations(
    database: Database, selections: tuple[ComputationSelection, ...]
) -> None:
    row = database.rows["final_results"][0]
    description = ResultDescription.model_validate_json(row["description"])
    description.outputs["a"] = description.outputs["a"].model_copy(
        update={"computations": selections}
    )
    row["description"] = description.model_dump_json()


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 100])
@pytest.mark.parametrize("overlap", [False, True])
def test_exact_traversal_across_selected_computations(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    *,
    overlap: bool,
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    expected = [("same", "a", 10), ("same", "z", 30), ("next", "r", 2)]
    if overlap:
        # Identical bare addresses in separate computations must remain distinct.
        select_computations(
            database,
            (
                ComputationSelection(value_key="cached", groups=("empty",)),
                ComputationSelection(value_key="cached", groups=("same",)),
                ComputationSelection(value_key="fresh", groups=()),
                ComputationSelection(value_key="fresh", groups=("same",)),
                ComputationSelection(value_key="fresh", groups=("empty",)),
            ),
        )
        expected = [*expected[:2], ("same", "a", 1), ("same", "z", 3)]
    before = deepcopy(database.rows)
    forbidden = Mock(side_effect=AssertionError("Pagination cannot read full frames"))
    monkeypatch.setattr(OutputSelection, "frame", forbidden)
    monkeypatch.setattr(MetricTables, "read_connection", forbidden)
    reader = ResultStore(store)
    observed = []
    cursor = None
    for _ in range(len(expected) + 1):
        candidates = page(reader, reference, limit=limit, cursor=cursor)
        assert 0 < len(candidates) <= limit
        observed.extend(candidate.cells for candidate in candidates)
        cursor = candidates[-1].next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("Cursor traversal did not terminate")
    assert observed == expected
    assert database.rows == before


@pytest.mark.parametrize(
    ("count", "limit"),
    [
        *((count, limit) for count in (0, 1, 2, 5) for limit in (1, 2, 1024)),
        (1025, 1024),
    ],
)
def test_duplicate_valued_unpartitioned_rows(
    tmp_path: Path, count: int, limit: int
) -> None:
    path = tmp_path / "results.sqlite"
    _, reference = retained_service(
        path,
        pa.DataFrameSchema(
            {
                "value": pa.Column(
                    pd.Int64Dtype(), description="Value", metadata={"unit": None}
                )
            },
            strict=True,
        ),
        pd.DataFrame({"value": pd.array([7] * count, dtype="Int64")}),
    )
    with ResultStore.open(path) as reader:
        observed = []
        cursor = None
        cursors = set()
        while True:
            candidates = page(reader, reference, "rows", limit, cursor)
            observed.extend(candidate.cells for candidate in candidates)
            assert len(candidates) <= limit
            cursor = candidates[-1].next_cursor if candidates else None
            if cursor is None:
                break
            assert cursor not in cursors
            cursors.add(cursor)
        assert observed == [(7,)] * count


@pytest.mark.parametrize(
    "selections", [(), (ComputationSelection(value_key="cached", groups=()),)]
)
def test_empty_selection_has_no_candidates(
    database: Database, tmp_path: Path, selections: tuple[ComputationSelection, ...]
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    select_computations(database, selections)
    assert page(ResultStore(store), reference) == []


def test_prefetched_candidate_can_be_retried(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    reader = ResultStore(store)
    with reader.page_candidates(reference, "a", 3) as selected:
        first = next(selected.rows)
        rejected = next(selected.rows)
        assert first.next_cursor is not None
    assert list(selected.rows) == []
    for _ in range(2):
        # Repeated rejection must never consume the oversized candidate.
        with reader.page_candidates(reference, "a", 1, first.next_cursor) as resumed:
            assert next(resumed.rows) == rejected
    last = page(reader, reference, cursor=rejected.next_cursor)
    assert [candidate.cells for candidate in last] == [("next", "r", 2)]
    assert last[-1].next_cursor is None


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "2", None])
def test_invalid_limits_preserve_context_without_storage(
    database: Database, tmp_path: Path, limit: int
) -> None:
    reader = ResultStore(SQLiteStore(tmp_path / "results.sqlite"))
    database.engine.connect.side_effect = AssertionError("Invalid bounds reached SQL")
    with pytest.raises(InvalidPaginationError) as caught:
        page(reader, "a" * 32, "snow", limit)
    assert vars(caught.value) == {
        "job_id": "a" * 32,
        "output_name": "snow",
        "input_name": None,
        "parameter": "limit",
    }


@pytest.mark.parametrize("cursor", ["", "bad", "雪", "e30=", "bnVsbA==", 1, [], {}])
def test_malformed_cursors_preserve_context_without_storage(
    database: Database, tmp_path: Path, cursor: str
) -> None:
    reader = ResultStore(SQLiteStore(tmp_path / "results.sqlite"))
    database.engine.connect.side_effect = AssertionError("Invalid cursor reached SQL")
    with pytest.raises(InvalidPaginationError) as caught:
        page(reader, "a" * 32, "snow", cursor=cursor)
    assert vars(caught.value) == {
        "job_id": "a" * 32,
        "output_name": "snow",
        "input_name": None,
        "parameter": "cursor",
    }


@pytest.mark.parametrize("target", ["result", "output"])
def test_cursors_are_bound_to_exact_result_and_output(
    tmp_path: Path, target: str
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    reader = ResultStore(store)
    cursor = page(reader, reference, limit=1)[0].next_cursor
    requested_ref = "f" * 32 if target == "result" else reference
    requested_output = "other" if target == "output" else "a"
    with pytest.raises(InvalidPaginationError) as caught:
        page(reader, requested_ref, requested_output, cursor=cursor)
    assert caught.value.job_id == requested_ref
    assert caught.value.output_name == requested_output
    assert caught.value.parameter == "cursor"


@pytest.mark.parametrize("key", [-(2**63) - 1, 2**63, 10**100])
def test_numeric_cursor_keys_must_fit_sql_integers(
    database: Database, tmp_path: Path, key: int
) -> None:
    reader = ResultStore(SQLiteStore(tmp_path / "results.sqlite"))
    database.engine.connect.side_effect = AssertionError("Invalid cursor reached SQL")
    cursor = base64.urlsafe_b64encode(
        json.dumps(
            {
                "version": 1,
                "job_id": "a" * 32,
                "output_name": "rows",
                "computation": 0,
                "key": [key],
            },
            separators=(",", ":"),
        ).encode()
    ).decode()
    with pytest.raises(InvalidPaginationError) as caught:
        page(reader, "a" * 32, "rows", cursor=cursor)
    assert caught.value.parameter == "cursor"


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"computation": -1},
        {"computation": True},
        {"computation": 20},
        {"key": ["same"]},
        {"key": ["same", 1]},
        {"key": ["absent", "r"]},
        {"key": ["next", "r"]},  # Exists but is not in this selected group.
        {"unknown": 1},
    ],
)
def test_invalid_cursor_positions(tmp_path: Path, change: dict) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    reader = ResultStore(store)
    cursor = page(reader, reference, limit=1)[0].next_cursor
    assert cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(cursor))
    payload.update(change)
    forged = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    with pytest.raises(InvalidPaginationError) as caught:
        page(reader, reference, cursor=forged)
    assert caught.value.parameter == "cursor"


@pytest.mark.parametrize("limit", [1, 2, 3, 100])
def test_queries_bound_selected_values_and_use_only_aggregate_validation(
    database: Database, tmp_path: Path, limit: int
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    queries = []

    def execute(statement: sa.Select, parameters: object = None) -> object:
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        if source.name.startswith("data_"):
            queries.append(statement)
        return database.execute(statement, parameters)

    database.connection.execute.side_effect = execute
    with ResultStore(store).page_candidates(reference, "a", limit) as selected:
        assert queries
        assert all("count(" in str(query) for query in queries)
        queries.clear()
        assert len(list(selected.rows)) == min(limit, 3)
    for query in queries:
        compiled = query.compile(dialect=sqlite.dialect())
        sql = str(compiled).upper()
        assert "ORDER BY" in sql
        assert "LIMIT" in sql
        assert "LOCATION_ID IN" in sql
        assert "WHERE" in sql
        assert "DISTINCT" not in sql
        assert 0 < compiled.params["param_1"] <= limit + 1


@pytest.mark.parametrize("exit_kind", ["early", "consumer-error", "fetch-error"])
def test_page_resources_close_on_early_exit(
    database: Database, tmp_path: Path, exit_kind: str
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    cursors = []
    database.connection.reset_mock()

    def execute(statement: sa.Select, parameters: object = None) -> object:
        cursor = database.execute(statement, parameters)
        if "LIMIT" in str(statement):
            cursors.append(cursor)
            if exit_kind == "fetch-error":
                cursor.mappings.side_effect = RuntimeError("fetch failed")
        return cursor

    database.connection.execute.side_effect = execute
    expectation = (
        nullcontext()
        if exit_kind == "early"
        else pytest.raises(RuntimeError, match="failed")
    )
    with expectation, ResultStore(store).page_candidates(reference, "a") as selected:
        next(selected.rows)
        if exit_kind == "consumer-error":
            msg = "consumer failed"
            raise RuntimeError(msg)
    assert cursors
    assert all(cursor.close.called for cursor in cursors)
    database.connection.__exit__.assert_called()
    assert list(selected.rows) == []


@pytest.mark.parametrize("damage", ["report", "rows", "scope", "partitions"])
def test_pagination_preserves_required_integrity_checks(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    if damage == "report":
        database.rows["final_results"][0]["report"] = None
    elif damage == "rows":
        data = next(iter(store.layout.tables.values()))
        database.rows[data.table.name].clear()
    elif damage == "scope":
        select_computations(
            database,
            (ComputationSelection(value_key="other-provider", groups=("same",)),),
        )
    else:
        database.rows["stored_partitions"].clear()
    with pytest.raises(ResultIntegrityError) as caught:
        page(ResultStore(store), reference)
    assert caught.value.job_id == reference
    assert caught.value.output_name == "a"


@pytest.mark.parametrize("target", ["malformed", "missing", "output", "closed"])
def test_pagination_preserves_lookup_failures(tmp_path: Path, target: str) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    _, reference = mixed_result(store)
    reader = ResultStore(store)
    if target == "closed":
        reader.close()
    expected = {
        "malformed": ValidationError,
        "missing": ResultNotFoundError,
        "output": ResultNotFoundError,
        "closed": RuntimeError,
    }[target]
    with pytest.raises(expected):
        page(
            reader,
            "bad"
            if target == "malformed"
            else "f" * 32
            if target == "missing"
            else reference,
            "missing" if target == "output" else "a",
        )
