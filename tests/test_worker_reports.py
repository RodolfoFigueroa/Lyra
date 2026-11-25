"""Durable worker identity, report parity, and failed publication behavior."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import Field
from rq.job import Job, JobStatus
from sqlalchemy.sql.dml import Insert

from lyra.api.client.results import successful_result
from lyra.api.exceptions import MetricRunError
from lyra.contracts.config import JobsConfig
from lyra.contracts.errors import (
    ExecutionPersistenceError,
    ReportNotFoundError,
    ResultNotFoundError,
)
from lyra.contracts.inspection import MAX_INSPECTION_BYTES
from lyra.contracts.jobs import FailedJobResult, TableJobResult
from lyra.contracts.references import new_job_id
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import MetricParameters, PluginDefinition, RunContext, metric
from lyra.server import worker
from lyra.server.job_store import JobStore
from lyra.server.results import ResultService
from tests.queue_helpers import accepted_envelope, read_retained
from tests.sqlite_helpers import Database, database, fail_insert
from tests.worker_graph_integration_helpers import VALUES, install, native_job

__all__ = ["database"]

pytestmark = pytest.mark.usefixtures("worker_state")


class Parameters(MetricParameters):
    value: int = Field(7, description="Value")
    label: str | None = Field(None, description="Label")


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("large", [False, True])
def test_worker_local_and_development_report_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, empty: bool, large: bool
) -> None:
    calls = []

    @metric(name="root", description="Root", outputs={"answer": VALUES}, cache="full")
    def root(*, parameters: Parameters, context: RunContext) -> pd.DataFrame:
        calls.append((context.job_id, parameters.value))
        return pd.DataFrame(
            {"value": pd.Series([] if empty else [parameters.value], dtype="int64")}
        )

    _, runtime = install(tmp_path, monkeypatch, [root])
    runner = LocalRunner.from_definitions(
        {"local": PluginDefinition(metrics=[root])},
        result_store_path=tmp_path / "local.sqlite",
    )
    label = "x" * (MAX_INSPECTION_BYTES + 1) if large else None
    local = runner.run("root", parameters={"label": label})
    development = runner.run_development("root", parameters={"label": label})
    native = native_job(monkeypatch)
    reports = []
    for _ in range(2):
        native.id = new_job_id()
        envelope = accepted_envelope(
            job_id=native.id, metric="root", inputs={"parameters": {"label": label}}
        )

        handoff = worker.run_metric_task(envelope.model_dump(mode="json"))
        restored = read_retained(str(handoff["job_id"]))
        report = restored.report
        assert handoff["job_id"] == report.job_id == restored.metadata.job_id
        assert report.job_id == native.id
        assert report.request == envelope.accepted.plan.request == local.report.request
        assert report.request.parameters == {"value": 7, "label": label}
        if large:
            assert len(report.model_dump_json().encode()) > MAX_INSPECTION_BYTES
        assert report.nodes[0].parameters == local.report.nodes[0].parameters
        assert report.nodes[0].outputs == local.report.nodes[0].outputs
        assert restored.tables["answer"].value.tolist() == ([] if empty else [7])
        native.args = (envelope.model_dump(mode="json"),)
        assert_public_job_identity(
            native, runtime.config.result_store_path, handoff, monkeypatch
        )
        reports.append(report)
    assert reports[0].job_id != reports[1].job_id
    assert [report.nodes[0].outcome for report in reports] == ["executed", "reused"]
    assert development.report.nodes[0].outcome == "reused"
    assert calls == [(local.metadata.job_id, 7), (reports[0].job_id, 7)]
    native.meta.clear()
    assert read_retained(reports[0].job_id).report == reports[0]


def assert_public_job_identity(
    native: SimpleNamespace,
    path: Path,
    handoff: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native.key = f"rq:job:{native.id}"
    native.created_at = native.started_at = native.ended_at = datetime.now(UTC)
    native.get_status = Mock(return_value=JobStatus.FINISHED)
    native.latest_result = Mock(return_value=SimpleNamespace(return_value=handoff))
    monkeypatch.setattr(Job, "fetch", Mock(return_value=native))
    jobs = JobStore(Mock(ttl=Mock(return_value=60)), JobsConfig())
    status = jobs.read_status(native.id)
    # Public reads use the same isolated database boundary as the worker.
    monkeypatch.setattr(
        ResultStore, "open", lambda path: ResultStore(worker.SQLiteStore(path))
    )
    service = ResultService(jobs, path)
    observation = service.read_observation(native.id)
    assert observation is not None
    result = service.read_result(observation)
    assert isinstance(result, TableJobResult)
    inspection = service.inspect(observation)
    assert status is not None
    with ResultStore.open(path) as reader:
        retained = reader.read_result(native.id)
    assert (
        status.job_id
        == result.job_id
        == inspection.job_id
        == retained.report.job_id
        == retained.metadata.job_id
        == handoff["job_id"]
        == native.id
    )


@pytest.mark.parametrize("boundary", ["setup", "values", "publication", "commit"])
def test_worker_persistence_failure_uses_failed_job_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, database: Database
) -> None:
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root() -> pd.DataFrame:
        assert not database.in_transaction
        return pd.DataFrame({"value": [7]})

    _, runtime = install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    cause = OSError("required persistence failed")
    if boundary == "commit":

        def inject(statement: object, parameters: object = None) -> object:
            result = database.execute(statement, parameters)
            if (
                isinstance(statement, Insert)
                and statement.table.name == "final_results"
            ):
                monkeypatch.setattr(database, "commit", Mock(side_effect=cause))
            return result

        database.connection.execute.side_effect = inject
    elif boundary == "publication":
        fail_insert(database, monkeypatch, "final_results", cause)
    else:
        writer = {
            "setup": SQLiteStore,
            "values": MetricTables,
        }[boundary]
        monkeypatch.setattr(
            writer,
            "initialize" if boundary == "setup" else "publish",
            Mock(side_effect=cause),
        )
    with pytest.raises(GraphExecutionError) as caught:
        worker.run_metric_task(envelope.model_dump(mode="json"))
    assert caught.value.__cause__ is not None
    assert caught.value.__cause__.__cause__ is cause
    failed = FailedJobResult.model_validate(native.meta["failure"])
    assert failed.error["type"] == "worker"
    with pytest.raises(MetricRunError):
        successful_result(failed)
    assert not native.meta["execution"]["succeeded"]
    assert not database.rows.get("final_results")
    persistence_error = caught.value.__cause__
    assert persistence_error is not None
    assert isinstance(persistence_error, ExecutionPersistenceError)
    assert persistence_error.job_id == failed.job_id == native.id
    path = runtime.config.result_store_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    with ResultStore.open(path) as reader:
        with pytest.raises(ReportNotFoundError) as absent_report:
            reader.read_report(failed.job_id)
        with pytest.raises(ResultNotFoundError) as absent_description:
            reader.describe(failed.job_id)
    assert (
        absent_report.value.job_id == absent_description.value.job_id == failed.job_id
    )


def test_worker_revalidated_parameters_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Accepted defaults survive a changed model default during worker revalidation.
    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root(*, parameters: Parameters) -> pd.DataFrame:
        return pd.DataFrame({"value": [parameters.value]})

    install(tmp_path, monkeypatch, [root])
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    monkeypatch.setattr(Parameters.model_fields["value"], "default", 99)
    Parameters.model_rebuild(force=True)
    try:
        result = worker.execute_job(
            envelope.model_dump(mode="json"), cast("Job", native)
        )
        retained = read_retained(str(result["job_id"]))
        assert retained.report.request.parameters["value"] == 7
        assert retained.tables["answer"].value.tolist() == [7]
    finally:
        Parameters.model_fields["value"].default = 7
        Parameters.model_rebuild(force=True)
