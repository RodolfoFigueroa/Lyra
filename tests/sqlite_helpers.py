"""Shared isolated provider/storage test boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.sql.dml import Insert

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
            source = statement.get_final_froms()[0]
            assert isinstance(source, sa.Table)
            table = source.name
            rows = self.rows.get(table, [])
            params = statement.compile().params
            if params:
                column = (
                    "registration_key"
                    if table == "storage_registrations"
                    else "execution_id"
                    if table == "final_results" and "execution_id_1" in params
                    else "result_ref"
                    if table == "final_results"
                    else "value_key"
                )
                rows = [
                    row
                    for row in rows
                    if (
                        row[column] in next(iter(params.values()))
                        if isinstance(next(iter(params.values())), (list, tuple, set))
                        else row[column] == next(iter(params.values()))
                    )
                ]
            if "location_id_1" in params:
                rows = [
                    row for row in rows if row["location_id"] in params["location_id_1"]
                ]
            result.mappings.return_value.__iter__.return_value = iter(deepcopy(rows))
            result.mappings.return_value.one_or_none.return_value = (
                deepcopy(rows[0]) if rows else None
            )
            result.scalar_one_or_none.return_value = (
                next(iter(rows[0].values())) if rows else None
            )
        return result


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Database:
    database = Database()
    monkeypatch.setattr(sa, "create_engine", MagicMock(return_value=database.engine))
    monkeypatch.setattr(event, "listen", MagicMock())
    monkeypatch.setattr(sa.MetaData, "create_all", MagicMock())
    return database
