"""Worker publication shares immutable values and retains independent results."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rq.job import JobStatus

from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server import job_store, worker
from tests.local_persistence_helpers import definition
from tests.queue_helpers import accepted_envelope
from tests.sqlite_helpers import Database, database
from tests.worker_graph_integration_helpers import install_definitions, native_job

__all__ = ["database"]


def test_jobs_share_values_with_local_and_survive_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: Database
) -> None:
    calls = []
    plugin = definition(calls)
    _, runtime = install_definitions(tmp_path, monkeypatch, {"plugin": plugin})
    assert runtime.results.result_store_path == runtime.config.result_store_path
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    local_result = LocalRunner.from_definitions(
        {"plugin": plugin}, result_store_path=runtime.config.result_store_path
    ).run("root")
    first = worker.run_metric_task(envelope.model_dump(mode="json"))
    native.id = "second-job"
    second_envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    second = worker.run_metric_task(second_envelope.model_dump(mode="json"))
    refs = [local_result.metadata.result_ref, first["result_ref"], second["result_ref"]]
    assert len(set(refs)) == 3
    assert calls == [[1]]
    assert len(database.rows["stored_results"]) == 1
    native.args = (second_envelope.model_dump(mode="json"),)
    native.key = f"rq:job:{native.id}"
    native.created_at = native.started_at = native.ended_at = datetime.now(UTC)
    native.get_status = Mock(return_value=JobStatus.FINISHED)
    native.latest_result = Mock(return_value=SimpleNamespace(return_value=second))
    monkeypatch.setattr(job_store.Job, "fetch", Mock(return_value=native))
    monkeypatch.setattr(runtime.jobs.connection, "ttl", Mock(return_value=60))
    observed = runtime.jobs.read_job(native.id)
    assert observed is not None
    assert observed.result_ref == second["result_ref"]
    monkeypatch.setattr(runtime.jobs.connection, "ttl", Mock(return_value=-2))
    assert runtime.jobs.read_job(native.id) is None
    # A restart and changed cache policy cannot remove previously published results.
    install_definitions(
        tmp_path, monkeypatch, {"plugin": definition(calls, cache="off")}
    )
    native_job(monkeypatch)
    envelope = accepted_envelope(job_id="graph-job", metric="root", inputs={})
    third = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert third["result_ref"] not in refs
    assert calls == [[1], [1]]
    assert_retained(runtime.config.result_store_path, refs)
    assert not list(tmp_path.rglob("*.arrow"))
    assert database.engine.dispose.call_count >= 5


@pytest.mark.parametrize("failure", ["value", "description"])
def test_required_publication_failure_fails_job_and_closes_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: Database, failure: str
) -> None:
    install_definitions(tmp_path, monkeypatch, {"plugin": definition([], cache="off")})
    native = native_job(monkeypatch)
    envelope = accepted_envelope(job_id=native.id, metric="root", inputs={})
    writer = MetricTables if failure == "value" else ResultDescriptions
    monkeypatch.setattr(writer, "publish", Mock(side_effect=OSError("full")))
    with pytest.raises(GraphExecutionError, match="full"):
        worker.run_metric_task(envelope.model_dump(mode="json"))
    assert native.meta["failure"]["error"]["type"] == "worker"
    assert not native.meta["execution"]["succeeded"]
    assert not database.rows.get("final_results")
    database.engine.dispose.assert_called_once()


def assert_retained(path: Path, refs: list) -> None:
    with SQLiteStore(path) as store:
        descriptions = []
        for ref in refs:
            description, tables = ResultDescriptions(store).read(ref)
            descriptions.append(description)
            assert description.parameters == {"items": [1]}
            assert tables["answer"].value.tolist() == [1]
        assert (
            descriptions[0].outputs
            == descriptions[1].outputs
            == descriptions[2].outputs
        )
