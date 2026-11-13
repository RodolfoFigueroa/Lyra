"""HTTP and MCP submissions execute live graphs with native result ownership."""

import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.server import worker
from tests.submission_execution_helpers import definitions, submit
from tests.worker_graph_integration_helpers import install_definitions, native_job

pytestmark = pytest.mark.usefixtures("worker_state")


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize(
    "scenario",
    [
        ("measure", "target"),
        ("measure", "source"),
        ("derived", "source"),
        ("hybrid", "source"),
        ("receiving", "source"),
        ("receiving_primary", "source"),
    ],
)
@pytest.mark.parametrize("ids", [["a", "b"], []])
def test_submission_native_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    scenario: tuple[str, str],
    ids: list[str],
) -> None:
    name, provider = scenario
    events = []
    plugins = definitions(events)
    _, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    worker.refresh_runner_registry("lightweight", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    inputs = {
        "source" if name in {"derived", "receiving_primary"} else "places": {
            "provider": provider,
            "ids": ids,
        }
    }
    runner = LocalRunner.from_definitions(plugins)
    local = runner.run(name, inputs=inputs, parameters=None)
    development = runner.run_development(name, inputs=inputs, parameters=None)
    assert local.metadata == development.metadata
    if name == "receiving":
        assert local.tables["total"].total.tolist() == [3 * len(ids)]
        assert local.metadata.outputs["total"].owner_scope is None
    else:
        assert local.tables["rows"].value.tolist() == [3] * len(ids)
        assert local.metadata.outputs["rows"].owner_scope is not None
        assert local.metadata.outputs["rows"].owner_scope.provider_id == "target"
    runtime.jobs.enqueue = Mock()
    assert asyncio.run(
        submit(
            runtime,
            transport,
            {"metric": name, "inputs": inputs, "cache_revision": "retained"},
        )
    )
    assert not events or events[-1][0] == "close"
    envelope = JobEnvelope.model_validate_json(
        runtime.jobs.enqueue.call_args.args[0].model_dump_json()
    )
    assert envelope.accepted.plan.parameters == {"weights": [3], "optional": None}
    assert envelope.accepted.plan.cache_revision == "retained"
    assert set(runtime.catalog.compiled.graph.nodes) >= {"summary", name}
    assert "summary" not in runtime.catalog.get_loaded_metric_names()
    original = deepcopy(envelope.model_dump(mode="json"))
    store = ArtifactStore(runtime.config.artifact_dir)
    for _ in range(2):
        native = native_job(monkeypatch, name)
        native.id = envelope.job_id
        key = str(worker.run_metric_task(original)["artifact_id"])
        tables = store.read(key)
        assert store.descriptions(key) == local.metadata.outputs
        for output, frame in local.tables.items():
            pd.testing.assert_frame_equal(tables[output], frame)
            pd.testing.assert_frame_equal(development.tables[output], frame)
        assert original == envelope.model_dump(mode="json")
    if ids:
        assert_lifecycles(events, routed=provider == "source")
    else:
        assert events == []


def assert_lifecycles(events: list, *, routed: bool) -> None:
    opened = [resource for event, resource in events if event == "open"]
    assert len(opened) == 4
    assert len({id(resource) for resource in opened}) == 4
    assert all(not resource.open for resource in opened)
    for resource in opened:
        lifecycle = [event for event, instance in events if instance is resource]
        assert lifecycle[0] == "open"
        assert lifecycle[-1] == "close"
        assert lifecycle.count("close") == 1
        assert "measure" in lifecycle
        if routed:
            assert "source" in lifecycle
            assert "target" in lifecycle
            assert lifecycle.count("close:source") == 1
            assert lifecycle.count("close:target") == 1
            assert lifecycle.index("close:source") < lifecycle.index("close")
            assert lifecycle.index("close:target") < lifecycle.index("close")


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("name", ["derived", "hybrid"])
def test_local_failure_releases_resources_before_retry(
    *, development: bool, name: str
) -> None:
    events = []
    runner = LocalRunner.from_definitions(definitions(events, fail=True))
    run = runner.run_development if development else runner.run
    inputs = {
        "source" if name == "derived" else "places": {
            "provider": "source",
            "ids": ["a", "b"],
        }
    }
    original = deepcopy(inputs)
    instances = []
    for _ in range(2):
        events.clear()
        with pytest.raises(GraphExecutionError, match="measurement failed"):
            run(name, inputs=inputs)
        opened = [resource for event, resource in events if event == "open"]
        assert len(opened) == 1
        instances.extend(opened)
        assert not opened[0].open
        assert events[-1] == ("close", opened[0])
        for event in (
            "source",
            "target",
            "measure",
            "close:source",
            "close:target",
            "close",
        ):
            assert events.count((event, opened[0])) == 1
        assert inputs == original
    assert instances[0] is not instances[1]


@pytest.mark.parametrize("transport", ["http", "mcp"])
@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "unknown",
        "derived",
        "null_inputs",
        "null_parameters",
        "route",
        "private",
    ],
)
def test_invalid_submission_never_acquires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    change: str,
) -> None:
    events = []
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions(events))
    runtime.jobs.enqueue = Mock()
    request = {
        "metric": "derived",
        "inputs": {"source": {"provider": "source", "ids": ["a"]}},
    }
    if change == "missing":
        request.pop("inputs")
    elif change in {"unknown", "derived"}:
        request["inputs"]["places" if change == "derived" else "unknown"] = {
            "provider": "target",
            "ids": [],
        }
    elif change.startswith("null_"):
        request[change.removeprefix("null_")] = None
    elif change == "route":
        request["inputs"]["source"]["provider"] = "target"
    else:
        request["metric"] = "summary"
    assert not asyncio.run(submit(runtime, transport, request))
    runtime.jobs.enqueue.assert_not_called()
    assert events == []


@pytest.mark.parametrize("mismatch", [False, True])
def test_worker_failure_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mismatch: bool,
) -> None:
    events = []
    _, runtime = install_definitions(
        tmp_path, monkeypatch, definitions(events, fail=True)
    )
    worker.refresh_runner_registry("lightweight", config=runtime.config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    runtime.jobs.enqueue = Mock()
    assert asyncio.run(
        submit(
            runtime,
            "http",
            {
                "metric": "hybrid",
                "inputs": {"places": {"provider": "source", "ids": ["a"]}},
            },
        )
    )
    envelope = runtime.jobs.enqueue.call_args.args[0]
    payload = envelope.model_dump(mode="json")
    native = native_job(monkeypatch, "hybrid")
    native.id = envelope.job_id
    if mismatch:
        payload["accepted"]["plan"]["build_revision"] = "changed"
    with pytest.raises(
        worker.DeploymentChangedError if mismatch else GraphExecutionError
    ):
        worker.run_metric_task(payload)
    error = native.meta["failure"]["error"]
    if mismatch:
        assert events == []
        assert error["type"] == "deployment_changed"
    else:
        assert error["node"] == "measure"
        assert error["dependency_path"] == ["hybrid", "measure"]
        assert events[-1][0] == "close"
        assert not events[-1][1].open
