"""Report-only retrieval checks durable descriptions without acquiring values."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rq.job import Job
from sqlalchemy.exc import DatabaseError

from lyra.contracts.errors import (
    InvalidExecutionIDError,
    ReportIntegrityError,
    ReportNotFoundError,
    ResultAccessError,
    ResultIntegrityError,
)
from lyra.contracts.reports import ExecutionReport
from lyra.runtime import provider_acquisition
from lyra.runtime.local import LocalRunner
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import loading
from test_support.publication import publish_result
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.fixture
def retained(database: Database, tmp_path: Path) -> tuple[Path, ExecutionReport]:
    path = tmp_path / "reports.sqlite"
    writer = SQLiteStore(path)
    publish_result(ResultDescriptions(writer), publish_values(writer, ("one",)))
    report = ExecutionReport.model_validate_json(
        database.rows["final_results"][0]["report"]
    )
    writer.close()
    path.touch()
    return path, report


def test_report_is_independent_of_jobs_values_and_plugins(
    retained: tuple[Path, ExecutionReport],
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, expected = retained
    # Expired jobs have no bearing on retained reports. Any job lookup is forbidden.
    monkeypatch.setattr(Job, "fetch", Mock(side_effect=AssertionError("Job expired")))
    for target, names in (
        (LocalRunner, ("__init__",)),
        (ResultDescriptions, ("_read_outputs",)),
        (SQLiteStore, ("initialize", "load_registrations", "header")),
        (loading, ("load_plugin_definition", "load_installed_plugin_definition")),
        (
            provider_acquisition,
            ("acquire_provider_input", "acquire_provider_candidates"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(target, name, Mock(side_effect=AssertionError(name)))
    # Report lookup does not sweep value storage, including unstored intermediates.
    database.rows = {"final_results": database.rows["final_results"]}
    before = deepcopy(database.rows)
    with ResultStore.open(path) as reader:
        report = reader.read_report(expected.execution_id)
        assert report == expected
        assert report.nodes is report.nodes
        report.request.parameters.clear()
        report.nodes[0].parameters.clear()
        report.nodes[0].outputs["a"].table_schema.columns.clear()
        assert reader.read_report(expected.execution_id) == expected
    assert report.result_ref == expected.result_ref
    with pytest.raises(RuntimeError, match="closed"):
        reader.read_report(expected.execution_id)
    with ResultStore.open(path) as reopened:
        restored = reopened.read_report(expected.execution_id)
    assert restored == expected
    assert database.rows == before


@pytest.mark.parametrize("execution_id", ["", "bad", "a" * 31, "A" * 32, "0" * 33])
def test_invalid_id(
    retained: tuple[Path, ExecutionReport], database: Database, execution_id: str
) -> None:
    database.connection.execute.side_effect = AssertionError("Must validate before SQL")
    with (
        ResultStore.open(retained[0]) as reader,
        pytest.raises(InvalidExecutionIDError) as captured,
    ):
        reader.read_report(execution_id)
    assert isinstance(captured.value, ResultAccessError)
    assert captured.value.execution_id == execution_id
    assert isinstance(captured.value.__cause__, ValueError)


def test_absent_attempt_report(retained: tuple[Path, ExecutionReport]) -> None:
    attempted_execution_id = uuid4().hex
    with (
        ResultStore.open(retained[0]) as reader,
        pytest.raises(ReportNotFoundError) as captured,
    ):
        reader.read_report(attempted_execution_id)
    assert isinstance(captured.value, ResultAccessError)
    assert captured.value.execution_id == attempted_execution_id


def test_table_and_description_do_not_decode_report(
    retained: tuple[Path, ExecutionReport], database: Database
) -> None:
    path, report = retained
    row = database.rows["final_results"][0]
    row["report"] = "{"
    with ResultStore.open(path) as reader:
        assert reader.describe(report.result_ref).execution_id == report.execution_id
        assert reader.read_table(report.result_ref, "a").value.tolist() == [1]
        with pytest.raises(ReportIntegrityError):
            reader.read_report(report.execution_id)
        with pytest.raises(ResultIntegrityError):
            reader.read_result(report.result_ref)
        row["report"] = None
        with pytest.raises(ResultIntegrityError) as captured:
            reader.read_table(report.result_ref, "a")
        assert captured.value.result_ref == report.result_ref
        assert captured.value.output_name == "a"


@pytest.mark.parametrize(
    "damage",
    [
        "report_json",
        "report_missing",
        "description_json",
        "description_missing",
        "execution_id",
        "result_ref",
        "metric",
        "parameters",
        "revision",
        "output_inventory",
        "output_selection",
        "owner",
    ],
)
@pytest.mark.parametrize("lookup", ["report", "result"])
def test_damaged_report_pair(
    retained: tuple[Path, ExecutionReport], database: Database, damage: str, lookup: str
) -> None:
    path, report = retained
    damage_pair(database, damage)
    error_type = ReportIntegrityError if lookup == "report" else ResultIntegrityError
    with ResultStore.open(path) as reader:
        read = reader.read_report if lookup == "report" else reader.read_result
        identifier = report.execution_id if lookup == "report" else report.result_ref
        with pytest.raises(error_type) as captured:
            read(identifier)
    assert isinstance(captured.value, ResultAccessError)
    if isinstance(captured.value, ReportIntegrityError):
        assert captured.value.execution_id == report.execution_id
    else:
        assert captured.value.result_ref == report.result_ref
        assert captured.value.output_name is None
    assert captured.value.__cause__ is not None


@pytest.mark.parametrize("boundary", ["connect", "execute"])
def test_storage_error_context(
    retained: tuple[Path, ExecutionReport], database: Database, boundary: str
) -> None:
    path, report = retained
    failure = DatabaseError("SELECT", {}, RuntimeError("damaged database"))
    if boundary == "connect":
        database.engine.connect.side_effect = failure
    else:
        database.connection.execute.side_effect = failure
    with (
        ResultStore.open(path) as reader,
        pytest.raises(ReportIntegrityError) as captured,
    ):
        reader.read_report(report.execution_id)
    assert captured.value.execution_id == report.execution_id
    assert captured.value.__cause__ is failure


def damage_pair(database: Database, damage: str) -> None:
    row = database.rows["final_results"][0]
    if damage.endswith("_json"):
        row[damage.removesuffix("_json")] = "{"
    elif damage.endswith("_missing"):
        row[damage.removesuffix("_missing")] = None
    elif damage in {"execution_id", "result_ref"}:
        saved = json.loads(row["report"])
        saved[damage] = uuid4().hex
        row["report"] = json.dumps(saved)
    else:
        saved = json.loads(row["description"])
        if damage == "metric":
            saved["metric_name"] = "other"
        elif damage == "parameters":
            saved["parameters"] = {}
        elif damage == "revision":
            saved["code_revision"] = "other"
        elif damage == "output_inventory":
            saved["output_names"] = ["a"]
        elif damage == "output_selection":
            del saved["outputs"]["b"]
        else:
            saved["outputs"]["a"]["owner_scope"]["provider_id"] = "other"
        row["description"] = json.dumps(saved)
