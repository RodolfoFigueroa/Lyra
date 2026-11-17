"""Managed local runs own persistent storage and detached retained results."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from unittest.mock import Mock

import pytest
from sqlalchemy.sql.dml import Insert

from lyra.contracts.errors import ExecutionPersistenceError, ReportNotFoundError
from lyra.contracts.inspection import MAX_INSPECTION_BYTES
from lyra.contracts.references import validate_execution_id
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from tests.local_persistence_helpers import definition
from tests.sqlite_helpers import Database, database, fail_insert

__all__ = ["database"]


@pytest.mark.parametrize("constructor", ["installed", "definitions"])
@pytest.mark.parametrize("path_kind", ["default", "relative", "absolute"])
def test_paths_are_captured_lazily(
    constructor: str,
    path_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database: Database,
) -> None:
    monkeypatch.chdir(tmp_path)
    plugin = definition([])
    loader = Mock(return_value=plugin)
    monkeypatch.setattr("lyra.runtime.local.load_installed_plugin_definition", loader)
    expected = tmp_path / (
        ".lyra/results.sqlite3" if path_kind == "default" else "custom/results.sqlite3"
    )
    kwargs: dict[str, Any] = (
        {}
        if path_kind == "default"
        else {
            "result_store_path": expected
            if path_kind == "absolute"
            else Path("custom/results.sqlite3")
        }
    )
    runner = (
        LocalRunner(["test"], **kwargs)
        if constructor == "installed"
        else LocalRunner.from_definitions({"test": plugin}, **kwargs)
    )
    database.engine.connect.assert_not_called()
    assert not expected.parent.exists()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = runner.run("root")
    assert result.metadata.store_path == expected
    assert result.metadata.result_ref
    assert expected.parent.is_dir()
    assert not (other / ".lyra").exists()
    database.engine.dispose.assert_called_once()


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("cache", ["full", "off"])
def test_consecutive_runs_retain_distinct_detached_results(
    *, private: bool, empty: bool, cache: Literal["full", "off"], database: Database
) -> None:
    calls = []
    runner = LocalRunner.from_definitions(
        {"test": definition(calls, cache=cache, private=private, empty=empty)}
    )
    run = runner.run_development if private else runner.run
    first = run("root")
    assert first.report.execution_id == first.metadata.execution_id
    assert first.report.result_ref == first.metadata.result_ref
    assert first.report.request.parameters == first.metadata.parameters
    root = next(
        node for node in first.report.nodes if node.use_id == first.report.root_use_id
    )
    assert root.parameters == first.metadata.parameters
    report_items = first.report.request.parameters["items"]
    assert isinstance(report_items, list)
    report_items.append(88)
    assert first.metadata.parameters == {"items": [1]}
    assert root.parameters == {"items": [1]}
    items = first.metadata.parameters["items"]
    assert isinstance(items, list)
    items.append(99)
    first.tables["answer"]["value"] = 99
    second = run("root")
    assert first.metadata.execution_id != second.metadata.execution_id
    assert second.report.request.parameters == {"items": [1]}
    assert first.metadata.result_ref != second.metadata.result_ref
    assert second.metadata.parameters == {"items": [1]}
    assert second.tables["answer"].value.tolist() == ([] if empty else [1])
    assert len(calls) == (1 if cache == "full" else 2)
    with SQLiteStore(second.metadata.store_path) as store:
        description, saved_report, tables = ResultDescriptions(store).read_complete(
            first.metadata.result_ref
        )
    assert saved_report.request.parameters == {"items": [1]}
    assert saved_report.execution_id == first.metadata.execution_id
    assert description.parameters == {"items": [1]}
    assert tables["answer"].value.tolist() == ([] if empty else [1])
    assert len(database.rows["final_results"]) == 2


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("failure", ["setup", "value", "description"])
def test_required_publication_failure_closes_store(
    failure: str, monkeypatch: pytest.MonkeyPatch, database: Database, *, private: bool
) -> None:
    runner = LocalRunner.from_definitions({"test": definition([], cache="off")})
    writer = {
        "setup": SQLiteStore,
        "value": MetricTables,
        "description": ResultDescriptions,
    }[failure]
    cause = RuntimeError("publication failure")
    if failure == "description":
        fail_insert(database, monkeypatch, "final_results", cause)
    else:
        monkeypatch.setattr(
            writer,
            "initialize" if failure == "setup" else "publish",
            Mock(side_effect=cause),
        )
    run = runner.run_development if private else runner.run
    with pytest.raises(ExecutionPersistenceError) as caught:
        run("root")
    assert caught.value.__cause__ is cause
    validate_execution_id(caught.value.execution_id)
    database.engine.dispose.assert_called_once()
    assert not database.rows.get("final_results")
    provenance = runner.inspect()
    assert provenance is not None
    assert not provenance.execution.succeeded


def test_disabling_cache_preserves_retained_results(
    tmp_path: Path, database: Database
) -> None:
    calls = []
    path = tmp_path / "results.sqlite3"
    enabled = LocalRunner.from_definitions(
        {"test": definition(calls)}, result_store_path=path
    )
    first = enabled.run("root")
    disabled = LocalRunner.from_definitions(
        {"test": definition(calls, cache="off")}, result_store_path=path
    )
    second = disabled.run("root")
    assert len(calls) == 2
    third = enabled.run("root")
    assert len(calls) == 2
    assert (
        len(
            {
                first.metadata.result_ref,
                second.metadata.result_ref,
                third.metadata.result_ref,
            }
        )
        == 3
    )
    with SQLiteStore(path) as store:
        _, retained = ResultDescriptions(store).read(first.metadata.result_ref)
    assert retained["answer"].value.tolist() == [1]
    assert len(database.rows["final_results"]) == 3


@pytest.mark.parametrize("private", [False, True])
def test_report_access_is_eager(
    monkeypatch: pytest.MonkeyPatch, database: Database, *, private: bool
) -> None:
    runner = LocalRunner.from_definitions({"test": definition([], private=private)})
    run = runner.run_development if private else runner.run
    result = run("root")
    retained_report = result.report.model_dump()
    run("root")
    forbidden = Mock(side_effect=AssertionError("report access contacted storage"))
    monkeypatch.setattr(database.engine, "connect", forbidden)
    monkeypatch.setattr(SQLiteStore, "__init__", forbidden)
    assert result.report is result.report
    assert result.report.model_dump() == retained_report
    assert result.report.execution_id == result.metadata.execution_id
    forbidden.assert_not_called()


@pytest.mark.parametrize("private", [False, True])
def test_store_construction_failure_has_attempt_identity(
    monkeypatch: pytest.MonkeyPatch, *, private: bool
) -> None:
    runner = LocalRunner.from_definitions({"test": definition([])})
    cause = OSError("cannot configure store")
    monkeypatch.setattr(SQLiteStore, "__init__", Mock(side_effect=cause))
    run = runner.run_development if private else runner.run
    with pytest.raises(ExecutionPersistenceError) as caught:
        run("root")
    validate_execution_id(caught.value.execution_id)
    assert caught.value.__cause__ is cause


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("boundary", ["values", "pair", "commit"])
def test_failed_transaction_and_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database: Database,
    boundary: str,
    *,
    private: bool,
) -> None:
    calls = []
    path = tmp_path / "retry.sqlite"
    plugin = definition(calls, private=private)
    runner = LocalRunner.from_definitions({"test": plugin}, result_store_path=path)
    run = runner.run_development if private else runner.run
    cause = OSError("transaction failed")
    attempted_pairs = []

    def inject(statement: object, parameters: object = None) -> object:
        result = database.execute(statement, parameters)
        if isinstance(statement, Insert):
            if statement.table.name == "final_results":
                assert database.in_transaction
                attempted_pairs.append(deepcopy(parameters))
                if boundary == "pair":
                    raise cause
            if statement.table.name == "stored_results" and boundary == "values":
                raise cause
        return result

    def commit() -> None:
        if boundary == "commit" and database.rows.get("final_results"):
            raise cause

    with monkeypatch.context() as patch:
        patch.setattr(database.connection.execute, "side_effect", inject)
        patch.setattr(database, "commit", commit)
        with pytest.raises(ExecutionPersistenceError) as caught:
            run("root")
    failed_id = caught.value.execution_id
    assert isinstance(failed_id, str)
    assert caught.value.__cause__ is cause
    assert not database.rows.get("final_results")
    assert bool(database.rows.get("stored_results")) == (boundary != "values")
    path.touch()
    with ResultStore.open(path) as reader:
        with pytest.raises(ReportNotFoundError) as absent:
            reader.read_report(failed_id)
        assert absent.value.execution_id == failed_id

    # A new runner can reuse complete values without any report for their producer.
    retry_runner = LocalRunner.from_definitions(
        {"test": plugin}, result_store_path=path
    )
    retry = retry_runner.run_development if private else retry_runner.run
    result = retry("root")
    assert result.metadata.execution_id != failed_id
    assert result.report.status == "succeeded"
    assert result.tables["answer"].value.tolist() == [1]
    assert len(calls) == (2 if boundary == "values" else 1)
    assert result.report.nodes[0].outcome == (
        "executed" if boundary == "values" else "reused"
    )
    if attempted_pairs:
        assert result.metadata.result_ref != attempted_pairs[0]["result_ref"]
    with ResultStore.open(path) as reader:
        assert reader.read_report(result.metadata.execution_id) == result.report
        assert reader.read_result(result.metadata.result_ref).report == result.report


def test_large_reports_and_many_executions_share_immutable_values(
    tmp_path: Path, database: Database
) -> None:
    calls = []
    path = tmp_path / "large.sqlite"
    runner = LocalRunner.from_definitions(
        {"test": definition(calls)}, result_store_path=path
    )
    items = list(range(MAX_INSPECTION_BYTES // 2))
    results = [runner.run("root", parameters={"items": items}) for _ in range(12)]
    assert len(calls) == 1
    assert len(database.rows["stored_results"]) == 1
    assert len({result.metadata.execution_id for result in results}) == len(results)
    assert len({result.metadata.result_ref for result in results}) == len(results)
    path.touch()
    for result in results:
        assert len(result.report.model_dump_json().encode()) > MAX_INSPECTION_BYTES
        with ResultStore.open(path) as reader:
            report = reader.read_report(result.metadata.execution_id)
            restored = reader.read_result(result.metadata.result_ref)
        assert report == result.report == restored.report
        assert report.request.parameters["items"] == items
        assert restored.tables["answer"].value.tolist() == [1]
