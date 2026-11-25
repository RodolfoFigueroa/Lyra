"""Submission, consumption, and admin inspection share one deployment queue."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rq import Queue

from lyra.contracts.jobs import JobStatusInfo
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import metric
from lyra.server import worker, worker_launcher
from lyra.server.routes.registration import register_http_routes
from test_support.table_fixtures import value_frame
from tests.worker_graph_integration_helpers import TABLE, install, native_job


@pytest.mark.usefixtures("worker_state")
@pytest.mark.parametrize("override", [None, "deployment-a"])
def test_graphs_share_the_configured_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    executed = []

    @metric(
        name="source", description="Source", code_revision="1", outputs={"data": TABLE}
    )
    def source(context: RunContext) -> pd.DataFrame:
        executed.append((context.job_id, context.node))
        return value_frame()

    @metric(
        name="derived",
        description="Derived",
        code_revision="1",
        inputs={"data": Dependency("data", producer="source")},
        outputs={"answer": TABLE},
    )
    def derived(data: pd.DataFrame, context: RunContext) -> pd.DataFrame:
        executed.append((context.job_id, context.node))
        return data

    _, runtime = install(tmp_path, monkeypatch, [source, derived])
    expected = override or "lyra"
    if override is not None:
        runtime.config.jobs.queue = override
    runtime.config.worker.concurrency = 3
    pool = Mock()
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool)
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=Mock())
    )
    monkeypatch.setattr(worker_launcher, "ensure_runtime_directories", Mock())
    monkeypatch.setattr(worker_launcher, "configure_logging", Mock())
    worker_launcher.launch_worker(config=runtime.config)
    (subscriptions,) = pool.call_args.args
    assert subscriptions == [expected]
    assert pool.call_args.kwargs["num_workers"] == 3

    app = FastAPI()
    register_http_routes(app)
    app.state.runtime = runtime
    with (
        TestClient(app) as client,
        patch.object(Queue, "enqueue_call", autospec=True) as enqueue,
    ):
        client.headers["Authorization"] = f"Bearer {runtime.credentials.agent_api_key}"
        ids = []
        for root in ("derived", "source"):
            response = client.post("/jobs", json={"metric": root, "inputs": {}})
            response.raise_for_status()
            ids.append(response.json()["job_id"])

    pending = [
        call.kwargs["args"][0]
        for call in enqueue.call_args_list
        if call.args[0].name in subscriptions
    ]
    assert [payload["job_id"] for payload in pending] == ids
    assert enqueue.call_count == 2

    _check_admin_observations(app, expected, ids, monkeypatch)

    for payload in pending:
        native_job(monkeypatch, job_id=payload["job_id"])
        assert worker.run_metric_task(payload) == {"job_id": payload["job_id"]}
    assert executed == [(ids[0], "source"), (ids[0], "derived"), (ids[1], "source")]


def _check_admin_observations(
    app: FastAPI, expected: str, ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = app.state.runtime
    now = datetime.now(UTC)
    valkey = runtime.valkey
    assert isinstance(valkey, Mock)
    queue_key = Queue(expected, connection=valkey).key
    valkey.llen.side_effect = {queue_key: len(ids)}.__getitem__
    valkey.lrange.side_effect = lambda key, start, end: {queue_key: ids}[key][
        start : end + 1
    ]
    valkey.smembers.side_effect = {
        f"rq:workers:{expected}": {"rq:worker:ready", "rq:worker:stale"}
    }.__getitem__
    valkey.hgetall.side_effect = {
        "rq:worker:ready": {"last_heartbeat": now.isoformat(), "state": "idle"},
        "rq:worker:stale": {"state": "idle"},
    }.__getitem__
    monkeypatch.setattr(
        runtime.jobs,
        "read_status",
        lambda job_id: JobStatusInfo(
            job_id=job_id, status="queued", created_at=now, updated_at=now
        ),
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {runtime.credentials.admin_api_key}"
        summary = client.get("/admin/queue")
        summary.raise_for_status()
        assert summary.json() == {
            "name": expected,
            "pending_depth": 2,
            "observed_workers": ["ready"],
        }
        workers = client.get("/admin/workers")
        workers.raise_for_status()
        assert {w["name"]: w["stale"] for w in workers.json()["workers"]} == {
            "ready": False,
            "stale": True,
        }
        page = client.get(
            "/admin/jobs", params={"status": "queued", "offset": 1, "limit": 1}
        )
        page.raise_for_status()
        assert [job["job_id"] for job in page.json()["jobs"]] == ids[1:]
