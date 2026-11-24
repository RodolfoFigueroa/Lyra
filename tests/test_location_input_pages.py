"""Bounded input selection and shared transport pages with isolated SQL storage."""

import base64
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects import sqlite

from lyra.contracts.errors import (
    InvalidPaginationError,
    LocationInputUnavailableError,
    ResultIntegrityError,
    ResultNotFoundError,
    RowTooLargeError,
)
from lyra.contracts.results import ResultRowPage
from lyra.contracts.retrieval_paths import (
    location_input_download_path,
    location_input_rows_path,
)
from lyra.runtime.input_selection import InputRows
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.operation_pairs import OperationPairs, operation_registration
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.response_budget import ResponseBudget, http_response_budget
from lyra.server.result_rows import RowPageBuilder
from test_support.publication import publish_result
from tests.location_input_helpers import publish_inputs, retained
from tests.result_description_helpers import publish_values
from tests.row_page_helpers import budget
from tests.sqlite_helpers import Database, database

__all__ = ["budget", "database"]
pytestmark = pytest.mark.usefixtures("database")
BASE = "https://lyra.example.test/public/api"


def union_result(store: SQLiteStore) -> str:
    direct, pairs = retained(store)
    operation = pairs.operations[0]
    second = operation.header.model_copy(
        update={"value_key": "second", "node_id": "other_operation"}
    )
    OperationPairs(store).publish(
        ((second, ("A", "B", "C")),), {"A": ("a",), "B": ("a", "b"), "C": ("b",)}
    )
    inputs = LocationInputs(store)
    selections = {
        "a": (direct, direct.model_copy(update={"records": ("a",)})),
        "pairs": (
            pairs.model_copy(
                update={"operations": (operation.model_copy(update={"targets": None}),)}
            ),
            pairs.model_copy(
                update={
                    "operations": (
                        operation.model_copy(
                            update={
                                "header": second,
                                "sources": ("A", "B"),
                                "targets": ("a",),
                            }
                        ),
                    )
                }
            ),
            pairs,
        ),
    }
    description = publish_values(store, ("one", "two"))
    description = description.model_copy(
        update={
            "location_inputs": InputBindings(
                names=tuple(selections),
                selections={
                    name: tuple(inputs.publish(selection) for selection in values)
                    for name, values in selections.items()
                },
            )
        }
    )
    return publish_result(ResultDescriptions(store), description)


@pytest.mark.parametrize("name", ["a", "pairs"])
@pytest.mark.parametrize("limit", [1, 2, 3, 100])
def test_union_traversal_without_eager_reads(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    limit: int,
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    reader = ResultStore(store)
    expected = reader.read_location_input(reference, name)
    # Input access does not depend on metric output values or historical reports.
    database.rows["final_results"][0]["report"] = "{"
    for registration in store.layout.descriptions.values():
        if registration.kind == "metric":
            database.rows.get(
                store.layout.tables[registration.key].table.name, []
            ).clear()
    forbidden = Mock(side_effect=AssertionError("eager input read"))
    monkeypatch.setattr(ResultStore, "read_location_input", forbidden)
    monkeypatch.setattr(LocationInputs, "frame", forbidden)
    monkeypatch.setattr(InputRows, "frame", forbidden)
    monkeypatch.setattr(OperationPairs, "read_connection", forbidden)
    builder = RowPageBuilder(reader, BASE, http_response_budget())
    cursor = None
    observed = []
    for _ in range(len(expected) + 1):
        page = builder.build_location_input(reference, name, limit, cursor)
        assert len(page.rows) <= limit
        observed.extend(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("Traversal did not terminate")
    keys = ["source_location_id", "location_id"] if name == "pairs" else ["location_id"]
    assert [tuple(row[key] for key in keys) for row in observed] == list(
        expected[keys].itertuples(index=False, name=None)
    )
    assert len(observed) == (3 if name == "pairs" else 2)
    native = gpd.GeoDataFrame.from_arrow(
        pa.ipc.open_stream(
            b"".join(reader.stream_location_input(reference, name))
        ).read_all()
    )
    pd.testing.assert_frame_equal(native, expected)


@pytest.mark.parametrize("name", ["a", "pairs"])
@pytest.mark.parametrize("limit", [1, 2, 100])
def test_value_queries_are_bounded(
    database: Database, tmp_path: Path, name: str, limit: int
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    observed = []

    def execute(statement: object, parameters: object = None) -> object:
        if isinstance(statement, sa.sql.Select):
            sql = str(
                statement.compile(
                    dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
                )
            )
            if "FROM data_" in sql and "count(" not in sql:
                assert "LIMIT" in sql
                bound = int(sql.split("LIMIT ", maxsplit=1)[1].split(maxsplit=1)[0])
                assert bound <= limit + 2
                observed.append(sql)
        return database.execute(statement, parameters)

    database.connection.execute.side_effect = execute
    with ResultStore(store).location_input_page_candidates(
        reference, name, limit
    ) as selected:
        rows = list(selected.rows)
    assert observed
    assert len(rows) <= limit


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "2", None])
def test_invalid_limit_does_not_reach_sql(
    database: Database, tmp_path: Path, limit: int
) -> None:
    reader = ResultStore(SQLiteStore(tmp_path / "inputs.sqlite"))
    database.engine.connect.side_effect = AssertionError("Invalid request reached SQL")
    with (
        pytest.raises(InvalidPaginationError) as caught,
        reader.location_input_page_candidates("a" * 32, "input", limit),
    ):
        pass
    assert vars(caught.value) == {
        "result_ref": "a" * 32,
        "input_name": "input",
        "output_name": None,
        "parameter": "limit",
    }


@pytest.mark.parametrize("cursor", ["", "bad", "雪", "e30=", "bnVsbA==", 1, [], {}])
def test_malformed_cursor_does_not_reach_sql(
    database: Database, tmp_path: Path, cursor: str
) -> None:
    reader = ResultStore(SQLiteStore(tmp_path / "inputs.sqlite"))
    database.engine.connect.side_effect = AssertionError("Invalid request reached SQL")
    with (
        pytest.raises(InvalidPaginationError) as caught,
        reader.location_input_page_candidates("a" * 32, "input", cursor=cursor),
    ):
        pass
    assert caught.value.parameter == "cursor"
    assert caught.value.input_name == "input"


@pytest.mark.parametrize(
    "change",
    [
        {"computation": 1},
        {"computation": True},
        {"key": ["C", "b"]},
        {"key": ["B", "b"]},
        {"key": ["A"]},
        {"key": [1, "a"]},
        {"interface": "output"},
        {"input_name": "a"},
        {"result_ref": "f" * 32},
    ],
)
def test_cursor_positions_and_context(tmp_path: Path, change: dict) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    reader = ResultStore(store)
    with reader.location_input_page_candidates(reference, "pairs", 1) as selected:
        cursor = next(selected.rows).next_cursor
    assert cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(cursor))
    payload.update(change)
    forged = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    with (
        pytest.raises(InvalidPaginationError) as caught,
        reader.location_input_page_candidates(reference, "pairs", cursor=forged),
    ):
        pass
    assert caught.value.parameter == "cursor"
    assert caught.value.output_name is None
    assert caught.value.input_name == "pairs"


def test_cursor_interfaces_do_not_alias(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    reader = ResultStore(store)
    # Both interfaces deliberately have the same name.
    with reader.page_candidates(reference, "a", 1) as selected:
        output_cursor = next(selected.rows).next_cursor
    with reader.location_input_page_candidates(reference, "a", 1) as selected:
        input_cursor = next(selected.rows).next_cursor
    assert output_cursor is not None
    assert input_cursor is not None
    with (
        pytest.raises(InvalidPaginationError),
        reader.location_input_page_candidates(reference, "a", cursor=output_cursor),
    ):
        pass
    with (
        pytest.raises(InvalidPaginationError),
        reader.page_candidates(reference, "a", cursor=input_cursor),
    ):
        pass


def test_encoded_boundaries_preserve_prefetched_row(
    tmp_path: Path, budget: ResponseBudget[BaseModel]
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    builder = RowPageBuilder(ResultStore(store), BASE, budget)
    singles = []
    cursor = None
    for _ in range(3):
        page = builder.build_location_input(reference, "pairs", 1, cursor)
        singles.append(page)
        cursor = page.next_cursor
    bound = max(map(budget.size, singles))
    bounded = replace(builder, budget=replace(budget, limit=bound))
    cursor = None
    observed = []
    while True:
        page = bounded.build_location_input(reference, "pairs", 100, cursor)
        assert budget.size(page) <= bound
        observed.extend(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert observed == [page.rows[0] for page in singles]
    too_small = replace(
        builder, budget=replace(budget, limit=budget.size(singles[1]) - 1)
    )
    for _ in range(2):
        with pytest.raises(RowTooLargeError) as caught:
            too_small.build_location_input(
                reference, "pairs", 1, singles[0].next_cursor
            )
        assert vars(caught.value) == {
            "result_ref": reference,
            "input_name": "pairs",
            "output_name": None,
            "download_url": (
                f"{BASE}/results/{reference}/location-inputs/pairs/download"
            ),
        }
    assert (
        builder.build_location_input(reference, "pairs", 1, singles[0].next_cursor)
        == singles[1]
    )


@pytest.mark.parametrize(
    "damage",
    [
        "record",
        "header",
        "partition",
        "pair",
        "duplicate",
        "completion",
        "schema",
        "selection",
    ],
)
def test_required_damage_is_integrity_failure(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, pairs = retained(store)
    reference = publish_inputs(store, {"pairs": pairs})
    data = database.rows[store.layout.tables[direct.registration.key].table.name]
    operations = database.rows[
        store.layout.tables[operation_registration("nearby").key].table.name
    ]
    if damage == "record":
        data.clear()
    elif damage == "pair":
        operations.clear()
    elif damage == "duplicate":
        operations.append(dict(operations[0]))
    elif damage == "header":
        database.rows["stored_results"][0]["node_id"] = "wrong"
    elif damage == "partition":
        database.rows["stored_partitions"][0]["location_id"] = "wrong"
    elif damage == "completion":
        next(
            row
            for row in database.rows["stored_results"]
            if row["value_key"] == "operation"
        )["output_rows"] = None
    elif damage == "schema":
        database.rows["storage_registrations"].clear()
    else:
        database.rows["input_selections"].clear()
    reader = ResultStore(SQLiteStore(store.path))
    with pytest.raises(ResultIntegrityError) as caught:
        RowPageBuilder(reader, BASE, http_response_budget()).build_location_input(
            reference, "pairs"
        )
    assert caught.value.input_name == "pairs"
    assert caught.value.output_name is None
    with pytest.raises(ResultIntegrityError):
        b"".join(reader.stream_location_input(reference, "pairs"))


def test_empty_unavailable_and_missing_preflight(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, pairs = retained(store)
    empty = direct.model_copy(update={"records": ()})
    skipped = empty.model_copy(
        update={
            "description": empty.description.model_copy(
                update={"availability": "not_materialized"}
            )
        }
    )
    zero = pairs.model_copy(
        update={
            "operations": (
                pairs.operations[0].model_copy(update={"sources": ("empty",)}),
            )
        }
    )
    reference = publish_inputs(
        store, {"empty": empty, "zero": zero, "skipped": skipped}
    )
    reader = ResultStore(store)
    builder = RowPageBuilder(reader, BASE, http_response_budget())
    for name in ("empty", "zero"):
        assert builder.build_location_input(reference, name) == ResultRowPage(
            rows=[], next_cursor=None
        )
        reader.validate_location_input(reference, name)
        table = pa.ipc.open_stream(
            b"".join(reader.stream_location_input(reference, name))
        ).read_all()
        assert table.num_rows == 0
        native = gpd.GeoDataFrame.from_arrow(table)
        pd.testing.assert_frame_equal(
            native, reader.read_location_input(reference, name)
        )
        assert native.crs is not None
        assert native.crs.to_epsg() == 4326
    for name, error in (
        ("skipped", LocationInputUnavailableError),
        ("unknown", ResultNotFoundError),
    ):
        with pytest.raises(error):
            next(reader.stream_location_input(reference, name))
        with pytest.raises(error):
            reader.validate_location_input(reference, name)
        with pytest.raises(error):
            builder.build_location_input(reference, name)


@pytest.mark.parametrize(
    ("name", "segment"), [("a/b 雪", "a%2Fb%20%E9%9B%AA"), ("..", "%2E%2E")]
)
def test_input_paths(name: str, segment: str) -> None:
    assert (
        location_input_download_path("a" * 32, name)
        == f"results/{'a' * 32}/location-inputs/{segment}/download"
    )
    assert (
        location_input_rows_path("a" * 32, name)
        == f"results/{'a' * 32}/location-inputs/{segment}/rows"
    )


def test_dense_pair_union_across_batches(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    _, selection = retained(store)
    sources = tuple(f"source-{i:04}" for i in range(550))
    operation = selection.operations[0]
    header = operation.header.model_copy(update={"value_key": "dense"})
    OperationPairs(store).publish(
        ((header, sources),), dict.fromkeys(sources, ("a", "b"))
    )
    selection = selection.model_copy(
        update={
            "operations": (
                operation.model_copy(
                    update={"header": header, "sources": sources, "targets": None}
                ),
                operation.model_copy(
                    update={
                        "header": header,
                        "sources": sources[100:400],
                        "targets": None,
                    }
                ),
            )
        }
    )
    reference = publish_inputs(store, {"pairs": selection})
    reader = ResultStore(store)
    expected = {(source, target) for source in sources for target in ("a", "b")}
    table = pa.ipc.open_stream(
        b"".join(reader.stream_location_input(reference, "pairs"))
    ).read_all()
    assert table.num_rows == len(expected)
    assert (
        set(
            zip(
                table["source_location_id"].to_pylist(),
                table["location_id"].to_pylist(),
                strict=True,
            )
        )
        == expected
    )
    builder = RowPageBuilder(reader, BASE, http_response_budget())
    observed = []
    cursor = None
    while True:
        page = builder.build_location_input(reference, "pairs", 250, cursor)
        observed.extend(
            (row["source_location_id"], row["location_id"]) for row in page.rows
        )
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(observed) == len(expected)
    assert set(observed) == expected
