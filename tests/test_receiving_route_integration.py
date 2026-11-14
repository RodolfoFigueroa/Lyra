"""Accepted receiving routes preserve values, resources, caches and stored results."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.sdk.parameters import MetricParameters
from lyra.server import worker
from tests.queue_helpers import read_retained
from tests.receiving_route_helpers import Trace, definitions, request
from tests.submission_execution_helpers import submit
from tests.worker_graph_integration_helpers import install_definitions, native_job

pytestmark = pytest.mark.usefixtures("worker_state")


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize("binding", ["matching", "nested", "same"])
@pytest.mark.parametrize("ids", [("a", "b"), ()])
def test_accepted_routes_execute_with_frozen_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    binding: str,
    ids: tuple[str, ...],
) -> None:
    accepting = True

    def defaults() -> list[int]:
        assert accepting, "Worker reevaluated an accepted default"
        return [2]

    class Parameters(MetricParameters):
        weights: list[int] = Field(default_factory=defaults, description="Weights")

    trace = Trace()
    plugins = definitions(trace, binding=binding, policy="full", options=Parameters)
    _, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    runner = LocalRunner.from_definitions(plugins)
    payload: dict = request(ids) | {"cache_revision": "receiving"}
    local = runner.run(**payload)
    trace.events.clear()
    trace.frames.clear()
    trace.parameters.clear()
    runtime.jobs.enqueue = Mock()
    assert asyncio.run(submit(runtime, transport, payload))
    assert trace.events == []
    envelope = JobEnvelope.model_validate_json(
        runtime.jobs.enqueue.call_args.args[0].model_dump_json()
    )
    assert envelope.accepted.plan.dependency_routes
    assert envelope.accepted.plan.cache_revision == "receiving"
    serialized = envelope.model_dump(mode="json")
    payload["parameters"]["weights"].append(-1)
    accepting = False
    for repeat in range(2):
        native = native_job(monkeypatch)
        native.id = envelope.job_id
        artifact = str(worker.run_metric_task(serialized)["result_ref"])
        tables = read_retained(artifact).tables
        pd.testing.assert_frame_equal(
            tables["total"], local.tables["total"].convert_dtypes()
        )
        assert tables["total"].total.tolist() == [90 if ids else 0]
        assert read_retained(artifact).metadata.outputs == local.metadata.outputs
        assert serialized == envelope.model_dump(mode="json")
        tables["total"].loc[:, "total"] = -1
        assert read_retained(artifact).tables["total"].total.tolist() == [
            90 if ids else 0
        ]
        if repeat or not ids:
            assert trace.parameters == []  # Full hits avoid operation/metric handlers.
        else:
            assert_receiving_trace(trace, binding)
        trace.events.clear()
        trace.parameters.clear()


def assert_receiving_trace(trace: Trace, binding: str) -> None:
    if binding != "nested":
        assert ("first", [2]) in trace.parameters
    assert ("second", [2]) in trace.parameters
    assert ("measure", [3]) in trace.parameters
    if binding == "nested":
        assert ("initial", [99]) in trace.parameters
        assert ("explicit", [99]) in trace.parameters
    assert trace.events[-1][0] == "close"
    shared = trace.events[-1][1]
    assert not shared.open
    assert all(instance is shared for _, instance in trace.events)
    for name in ("session", "handler", "source", "middle", "target"):
        assert trace.events.count((f"close:{name}", shared)) == 1


@pytest.mark.parametrize(
    "failure", ["mapping", "missing", "provider", "operation", "metric", "output"]
)
def test_receiving_worker_failure_closes_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    trace = Trace()
    _, runtime = install_definitions(
        tmp_path, monkeypatch, definitions(trace, binding="nested", failure=failure)
    )
    runtime.jobs.enqueue = Mock()
    assert asyncio.run(submit(runtime, "http", request()))
    envelope = runtime.jobs.enqueue.call_args.args[0]
    instances = []
    for _ in range(2):
        trace.events.clear()
        native = native_job(monkeypatch)
        native.id = envelope.job_id
        with pytest.raises(GraphExecutionError):
            worker.run_metric_task(envelope.model_dump(mode="json"))
        assert "root" not in trace.frames
        assert native.meta["failure"]["error"]["node"] != "root"
        shared = trace.events[-1][1]
        instances.append(shared)
        assert trace.events[-1] == ("close", shared)
        assert trace.events.count(("close", shared)) == 1
        assert not shared.open
        for provider in ("source", "middle", "target"):
            if (provider, shared) in trace.events:
                assert trace.events.count((f"close:{provider}", shared)) == 1
                assert trace.events.index((f"close:{provider}", shared)) < (
                    trace.events.index(("close:session", shared))
                )
    assert instances[0] is not instances[1]
