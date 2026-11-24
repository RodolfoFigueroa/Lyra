"""Worker loading and normalized transport validate receiving routes before I/O."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from pydantic import Field

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.jobs import JobCreateRequest
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.planning.parameters import receiving_key, route_key
from lyra.runtime.report_collection import ReportCollection
from lyra.sdk.parameters import MetricParameters
from lyra.server import job_submission, worker
from tests.dependency_route_helpers import definitions, request
from tests.queue_helpers import accepted_envelope
from tests.worker_graph_integration_helpers import install_definitions, native_job

if TYPE_CHECKING:
    from lyra.runtime.planning.bindings import BoundInvocation

pytestmark = pytest.mark.usefixtures("worker_state")


@pytest.mark.parametrize(
    "change",
    ["middle_provider", "target_provider", "origin"],
)
def test_old_route_rejected_by_worker_with_unchanged_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions())
    envelope = accepted_envelope(
        job_id="graph-job",
        metric="root",
        inputs=request()["inputs"],
        catalog=runtime.catalog.compiled,
    )
    _, current = install_definitions(
        tmp_path,
        monkeypatch,
        definitions({change: "2"}),
    )
    assert current.config.build_revision == envelope.accepted.plan.build_revision
    native = native_job(monkeypatch)
    acquire = Mock(side_effect=AssertionError("acquisition"))
    lookup = Mock(side_effect=AssertionError("cache lookup"))
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    monkeypatch.setattr(MetricTables, "lookup", lookup)
    with pytest.raises(worker.BoundPlanError, match="provider scopes"):
        worker.run_metric_task(envelope.model_dump(mode="json"))
    acquire.assert_not_called()
    lookup.assert_not_called()
    assert native.meta["failure"]["error"]["type"] == "bound_plan"


@pytest.mark.parametrize("damage", ["value", "missing_value"])
def test_worker_rejects_changed_serialized_receiving_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions())
    envelope = accepted_envelope(
        job_id="graph-job",
        metric="root",
        inputs=request()["inputs"],
        catalog=runtime.catalog.compiled,
    )
    payload = envelope.model_dump(mode="json")
    plan = payload["accepted"]["plan"]
    key = receiving_key("root", "inputs.left", "places")
    if damage == "value":
        plan["parameter_uses"][route_key(key, 0)]["count"] = "invalid"
    else:
        del plan["parameter_uses"][route_key(key, 0)]
    native = native_job(monkeypatch)
    acquire = Mock(side_effect=AssertionError("acquisition"))
    lookup = Mock(side_effect=AssertionError("cache lookup"))
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    monkeypatch.setattr(MetricTables, "lookup", lookup)
    with pytest.raises((GraphExecutionError, worker.BoundPlanError)):
        worker.run_metric_task(payload)
    acquire.assert_not_called()
    lookup.assert_not_called()
    assert native.meta["failure"]["error"]["type"] == "bound_plan"


def test_submission_carries_route_defaults_through_worker_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accepting = True

    def defaults() -> list[str]:
        assert accepting, "Worker evaluated an accepted route default"
        return ["accepted"]

    class Parameters(MetricParameters):
        count: int = Field(2, description="Count")
        tags: list[str] = Field(default_factory=defaults, description="Tags")

    _, runtime = install_definitions(
        tmp_path, monkeypatch, definitions(options=Parameters)
    )
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    submitted = request()
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest.model_validate(submitted),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: "graph-job",
        )
    )
    envelope = enqueue.call_args.args[0]
    key = receiving_key("root", "inputs.left", "places")
    assert envelope.accepted.plan.parameter_uses[route_key(key, 0)] == {
        "count": 2,
        "tags": ["accepted"],
    }
    submitted["parameters"]["tags"].append("caller mutation")
    accepting = False
    seen: list[BoundInvocation] = []

    def preflight(
        executor: GraphExecutor, plan: PreparedExecution, *, report: ReportCollection
    ) -> SimpleNamespace:
        # This phase verifies the worker acceptance boundary; routing execution is
        # deliberately outside this test's boundary.
        assert len(report.execution_id) == 32
        seen.append(executor.validate_plan(plan))
        return SimpleNamespace(result_ref="a" * 32, report_state=report)

    monkeypatch.setattr(GraphExecutor, "execute", preflight)
    native_job(monkeypatch)
    result = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert result["result_ref"] == "a" * 32
    assert len(str(result["execution_id"])) == 32
    assert len(seen) == 1
    route = seen[0].dependencies["left"].locations["places"].upstream
    assert route is not None
    assert route.parameter_values() == {"count": 2, "tags": ["accepted"]}
