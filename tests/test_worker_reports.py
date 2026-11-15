"""Durable worker identity, report parity, and failed publication behavior."""

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import Field
from sqlalchemy.sql.dml import Insert

from lyra.api.client.results import successful_result
from lyra.api.exceptions import MetricRunError
from lyra.contracts.errors import ExecutionPersistenceError, ReportNotFoundError
from lyra.contracts.inspection import MAX_INSPECTION_BYTES
from lyra.contracts.jobs import FailedJobResult
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import MetricParameters, PluginDefinition, metric
from lyra.server import worker
from tests.queue_helpers import accepted_envelope, read_retained
from tests.sqlite_helpers import Database, database
from tests.worker_graph_integration_helpers import VALUES, install, native_job

__all__ = ["database"]

if TYPE_CHECKING:
    from rq.job import Job

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
    def root(*, parameters: Parameters) -> pd.DataFrame:
        calls.append(parameters.value)
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
    envelope = accepted_envelope(
        job_id=native.id, metric="root", inputs={"parameters": {"label": label}}
    )
    reports = []
    for _ in range(2):
        handoff = worker.run_metric_task(envelope.model_dump(mode="json"))
        restored = read_retained(str(handoff["result_ref"]))
        report = restored.report
        assert (
            handoff["execution_id"]
            == report.execution_id
            == restored.metadata.execution_id
        )
        assert report.execution_id != native.id
        assert report.build_revision == runtime.config.build_revision
        assert report.request == envelope.accepted.plan.request == local.report.request
        assert report.request.parameters == {"value": 7, "label": label}
        if large:
            assert len(report.model_dump_json().encode()) > MAX_INSPECTION_BYTES
        assert report.nodes[0].parameters == local.report.nodes[0].parameters
        assert report.nodes[0].outputs == local.report.nodes[0].outputs
        assert restored.tables["answer"].value.tolist() == ([] if empty else [7])
        reports.append(report)
    assert reports[0].execution_id != reports[1].execution_id
    assert [report.nodes[0].outcome for report in reports] == ["executed", "reused"]
    assert development.report.nodes[0].outcome == "reused"
    assert calls == [7, 7]
    native.meta.clear()
    assert read_retained(reports[0].result_ref).report == reports[0]


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
    else:
        writer = {
            "setup": SQLiteStore,
            "values": MetricTables,
            "publication": ResultDescriptions,
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
    assert isinstance(persistence_error.execution_id, str)
    path = runtime.config.result_store_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    with ResultStore.open(path) as reader, pytest.raises(ReportNotFoundError) as absent:
        reader.read_report(persistence_error.execution_id)
    assert absent.value.execution_id == persistence_error.execution_id


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
        retained = read_retained(str(result["result_ref"]))
        assert retained.report.request.parameters["value"] == 7
        assert retained.tables["answer"].value.tolist() == [7]
    finally:
        Parameters.model_fields["value"].default = 7
        Parameters.model_rebuild(force=True)
