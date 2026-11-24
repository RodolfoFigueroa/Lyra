"""Shared isolated provider/storage test boundaries."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import event, exc
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    Grouping,
    Label,
    Null,
    UnaryExpression,
)
from sqlalchemy.sql.functions import Function
from sqlalchemy.sql.operators import is_not

if TYPE_CHECKING:
    from collections.abc import Generator


class Database:
    """Transaction-aware statement fake; never acquires an external resource."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.begin.side_effect = self.begin
        self.connection.execute.side_effect = self.execute
        self.engine = MagicMock()
        self.engine.connect.return_value = self.connection
        self.fail_commit = False
        self.commits = 0
        self.in_transaction = False

    @contextmanager
    def begin(self) -> Generator[None]:
        previous = deepcopy(self.rows)
        self.in_transaction = True
        try:
            yield
            self.commit()
        except BaseException:
            self.rows = previous
            raise
        else:
            self.commits += 1
        finally:
            self.in_transaction = False

    def commit(self) -> None:
        if self.fail_commit:
            msg = "commit failure"
            raise RuntimeError(msg)

    def execute(self, statement: object, parameters: object = None) -> MagicMock:
        result = MagicMock()
        if isinstance(statement, Insert):
            batch = parameters if isinstance(parameters, list) else [parameters]
            existing = self.rows.setdefault(statement.table.name, [])
            if statement.table.name == "final_results":
                for row in batch:
                    assert all(
                        row.get(key) is not None
                        for key in (
                            "execution_id",
                            "result_ref",
                            "description",
                            "report",
                        )
                    )
                    if any(
                        old[key] == row[key]
                        for old in existing
                        for key in ("execution_id", "result_ref")
                    ):
                        msg = "Duplicate execution or result identity"
                        raise ValueError(msg)
            existing.extend(deepcopy(batch))
        elif isinstance(statement, sa.sql.Select):
            rows = select_rows(statement, self.rows)
            result.mappings.return_value.__iter__.return_value = iter(deepcopy(rows))
            result.mappings.return_value.one_or_none.return_value = (
                deepcopy(rows[0]) if rows else None
            )
            result.scalar_one_or_none.return_value = (
                next(iter(rows[0].values())) if rows else None
            )
        return result


def select_rows(statement: sa.Select, tables: dict[str, list[dict]]) -> list[dict]:
    """Apply predicates, grouping, ordering and limits from the selected SQL."""
    source = statement.get_final_froms()[0]
    assert isinstance(source, sa.Table)
    rows = tables.get(source.name, [])
    if statement.whereclause is not None:
        rows = [row for row in rows if evaluate(statement.whereclause, row)]
    compiled = statement.compile()
    # Evaluate column expressions against the logical keys in the fake rows.
    order = statement._order_by_clauses  # ruff: ignore[private-member-access]
    if order:
        rows = sorted(
            rows, key=lambda row: tuple(evaluate(column, row) for column in order)
        )
    aggregate = any(
        isinstance(column, Label) and isinstance(column.element, Function)
        for column in statement.selected_columns
    )
    if aggregate:
        rows = aggregate_rows(statement, rows)
    else:
        rows = [
            {
                column.key: selected_value(column, row)
                for column in statement.selected_columns
            }
            for row in rows
        ]
    limit = compiled.limit_clause(statement).strip()
    if limit:
        assert limit.startswith("LIMIT :")
        rows = rows[: compiled.params[limit.removeprefix("LIMIT :")]]
    return rows


def aggregate_rows(statement: sa.Select, rows: list[dict]) -> list[dict]:
    """Project SQL aggregates, preserving empty ungrouped COUNT results."""
    group_by = statement._group_by_clauses  # ruff: ignore[private-member-access]
    groups: dict[tuple, list[dict]] = {}
    if not group_by:
        groups[()] = rows
    else:
        for row in rows:
            group = tuple(evaluate(column, row) for column in group_by)
            groups.setdefault(group, []).append(row)
    return [
        {
            column.key: aggregate_value(column, group)
            for column in statement.selected_columns
        }
        for group in groups.values()
    ]


def evaluate(expression: sa.ColumnElement, row: dict) -> object:
    """Evaluate the supported SQL selection subset, rejecting unknown predicates."""
    if isinstance(expression, Grouping):
        assert isinstance(expression.element, sa.ColumnElement)
        return evaluate(expression.element, row)
    if isinstance(expression, BindParameter):
        return expression.value
    if isinstance(expression, BooleanClauseList):
        values = [evaluate(clause, row) for clause in expression.clauses]
        assert expression.operator in {operators.and_, operators.or_}
        return all(values) if expression.operator is operators.and_ else any(values)
    if isinstance(expression, BinaryExpression):
        return comparison_value(expression, row)
    return selected_value(expression, row)


def comparison_value(expression: BinaryExpression, row: dict) -> bool:
    """Apply supported comparisons with SQL NULL and membership semantics."""
    left, right = evaluate(expression.left, row), evaluate(expression.right, row)
    if expression.operator is operators.in_op:
        assert isinstance(right, (tuple, list, set))
        return left in right
    if expression.operator is operators.is_not:
        return left is not right
    if left is None or right is None:
        return False
    if expression.operator is operators.eq:
        return left == right
    assert expression.operator is operators.gt
    assert isinstance(left, (str, int))
    assert isinstance(right, (str, int))
    if isinstance(left, str):
        assert isinstance(right, str)
        return left > right
    assert isinstance(right, int)
    return left > right


def aggregate_value(column: sa.ColumnElement, rows: list[dict]) -> object:
    """Honor COUNT, DISTINCT and grouped projections without a live SQL engine."""
    if isinstance(column, Label) and isinstance(column.element, Function):
        function = column.element
        assert function.name == "count"
        argument = next(iter(function.clauses))
        if argument.key == "*":
            return len(rows)
        if isinstance(argument, UnaryExpression):
            assert argument.operator is operators.distinct_op
            return len({evaluate(argument.element, row) for row in rows} - {None})
        return sum(evaluate(argument, row) is not None for row in rows)
    return selected_value(column, rows[0])


def selected_value(column: sa.ColumnElement, row: dict) -> object:
    """Project SQL columns and presence checks without fetching unselected bodies."""
    if isinstance(column, Null):
        return None
    if isinstance(column, Label):
        expression = column.element
        if isinstance(expression, sa.Column):
            return row[expression.key]
        assert isinstance(expression, BinaryExpression)
        assert expression.operator is is_not
        assert isinstance(expression.right, Null)
        return row[expression.left.key] is not None
    if (
        isinstance(column, sa.Column)
        and column.key not in row
        and isinstance(column.server_default, sa.DefaultClause)
    ):
        return column.server_default.arg
    return row[column.key]


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Database:
    database = Database()
    monkeypatch.setattr(sa, "create_engine", MagicMock(return_value=database.engine))
    monkeypatch.setattr(event, "listen", MagicMock())
    monkeypatch.setattr(sa.MetaData, "create_all", MagicMock())
    return database


@pytest.fixture
def schema_database(database: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    """Include table creation in the transaction snapshot for first-write tests."""

    def create_all(
        metadata: sa.MetaData,
        _connection: sa.Connection,
        tables: list[sa.Table] | None = None,
    ) -> None:
        for table in metadata.sorted_tables if tables is None else tables:
            database.rows.setdefault(table.name, [])

    def execute(statement: object, parameters: object = None) -> MagicMock:
        if isinstance(statement, sa.Select):
            table = statement.get_final_froms()[0]
            if isinstance(table, sa.Table) and table.name not in database.rows:
                cause = sqlite3.OperationalError(f"no such table: {table.name}")
                raise exc.OperationalError(str(statement), {}, cause)
        return database.execute(statement, parameters)

    def driver_sql(statement: str) -> MagicMock:
        result = MagicMock()
        if statement == "SELECT 1 FROM sqlite_schema LIMIT 1":
            result.scalar.return_value = 1 if database.rows else None
        else:
            assert statement in {"BEGIN", "BEGIN IMMEDIATE"}
        return result

    monkeypatch.setattr(sa.MetaData, "create_all", create_all)
    database.connection.execute.side_effect = execute
    database.connection.exec_driver_sql.side_effect = driver_sql
    return database


def fail_insert(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
    cause: Exception,
) -> None:
    """Inject a storage failure at a selected publication boundary."""
    execute = database.connection.execute.side_effect

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert) and statement.table.name == table:
            raise cause
        return execute(statement, parameters)

    monkeypatch.setattr(database.connection.execute, "side_effect", inject)
