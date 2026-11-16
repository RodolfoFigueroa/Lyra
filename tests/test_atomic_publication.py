"""Managed success publishes complete execution pairs after durable values."""

import json
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.sql.dml import Insert

from lyra.contracts.errors import ExecutionPersistenceError
from lyra.contracts.reports import ExecutionReport
from lyra.runtime.local import LocalRunner
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from lyra.sdk import PluginDefinition, metric
from test_support.publication import publish_result
from tests.provider_execution_helpers import RESULT
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("fault", [None, "value", "insert", "commit"])
def test_managed_atomic_success(
    database: Database, tmp_path: Path, fault: str | None, *, empty: bool
) -> None:
    calls = []

    @metric(name="total", description="Total", outputs={"total": RESULT}, cache="full")
    def total() -> pd.DataFrame:
        # Handlers must never execute under a storage publication transaction.
        assert not database.in_transaction
        calls.append(True)
        return pd.DataFrame({"total": pd.array([] if empty else [7], dtype="Int64")})

    path = tmp_path / "results.sqlite"
    runner = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[total])}, result_store_path=path
    )
    execute = database.execute

    def inject(statement: object, parameters: object = None) -> object:
        if isinstance(statement, Insert):
            if fault == "value" and statement.table.name == "stored_results":
                msg = "required value failure"
                raise RuntimeError(msg)
            if statement.table.name == "final_results":
                if fault == "insert":
                    msg = "pair insertion failure"
                    raise RuntimeError(msg)
                database.fail_commit = fault == "commit"
        return execute(statement, parameters)

    database.connection.execute.side_effect = inject
    if fault is not None:
        with pytest.raises(ExecutionPersistenceError) as caught:
            runner.run("total", inputs={})
        assert caught.value.execution_id is not None
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert not database.rows.get("final_results")
        assert bool(database.rows.get("stored_results")) == (fault != "value")
    database.connection.execute.side_effect = execute
    database.fail_commit = False
    first = runner.run("total", inputs={})
    second = runner.run("total", inputs={})
    rows = database.rows["final_results"]
    reports = [ExecutionReport.model_validate_json(row["report"]) for row in rows]
    assert len({report.execution_id for report in reports}) == len(rows)
    assert len({report.result_ref for report in reports}) == len(rows)
    assert first.metadata.result_ref != second.metadata.result_ref
    assert len(database.rows["stored_results"]) == 1
    assert len(calls) == (2 if fault == "value" else 1)
    assert reports[-1].nodes[0].outcome == "reused"
    for row, report in zip(rows, reports, strict=True):
        assert report.execution_id == row["execution_id"]
        assert report.result_ref == row["result_ref"]
        description, tables = ResultDescriptions(SQLiteStore(path)).read(
            row["result_ref"]
        )
        assert description.metric_name == report.request.metric
        assert tables["total"].total.tolist() == ([] if empty else [7])
    if fault is not None:
        assert caught.value.execution_id not in {r.execution_id for r in reports}


@pytest.mark.parametrize("damage", ["missing", "execution", "result", "root"])
def test_pair_integrity_on_read(
    database: Database, tmp_path: Path, damage: str
) -> None:

    store = SQLiteStore(tmp_path / "results.sqlite")
    writer = ResultDescriptions(store)
    ref = publish_result(writer, publish_values(store, ()))
    row = database.rows["final_results"][0]
    if damage == "missing":
        del row["report"]
    else:
        report = json.loads(row["report"])
        if damage == "root":
            report["nodes"][0]["parameters"] = {"different": True}
        else:
            report["execution_id" if damage == "execution" else "result_ref"] = "a" * 32
        row["report"] = json.dumps(report)
    with pytest.raises(DamagedDataError):
        writer.read_complete(ref)


def test_execution_cannot_publish_two_results(
    database: Database, tmp_path: Path
) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    writer = ResultDescriptions(store)
    description = publish_values(store, ())
    first = publish_result(writer, description)
    retained = database.rows["final_results"][0]
    report = ExecutionReport.model_validate_json(retained["report"])
    duplicate = report.model_copy(update={"result_ref": "b" * 32})
    with pytest.raises(ValueError, match="Duplicate"):
        writer.publish(description, duplicate)
    assert len(database.rows["final_results"]) == 1
    assert writer.read(first)[0] == description


def test_mismatched_pair_is_not_published(database: Database, tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "results.sqlite")
    writer = ResultDescriptions(store)
    description = publish_values(store, ())
    first = publish_result(writer, description)
    report = ExecutionReport.model_validate_json(
        database.rows["final_results"][0]["report"]
    )
    changed = description.model_copy(update={"parameters": {"different": True}})
    with pytest.raises(DamagedDataError, match="disagree"):
        writer.publish(changed, report)
    assert len(database.rows["final_results"]) == 1
    assert writer.read(first)[0] == description
