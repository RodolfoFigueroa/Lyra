"""Workers resolve selections with provider-only plugins in one managed scope."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.errors import UnresolvedLocationIDsError
from lyra.contracts.jobs import JobCreateRequest
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.sdk import loading
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import Resource
from lyra.server import job_submission, registry, worker
from lyra.server.plugins import InstalledPlugin
from lyra.server.runtime import ApplicationRuntime
from test_support.location_resolution import Provider, frame_features, selection
from tests.config_helpers import load_test_config
from tests.queue_helpers import run_and_read, submitted_input
from tests.runtime_helpers import make_runtime
from tests.worker_graph_integration_helpers import VALUES, native_job


@pytest.mark.parametrize(
    "failure",
    [None, "unknown", "invalid", "configuration", "setup", "resolve", "close"],
)
def test_worker_resolution_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events = []
    instances = []
    _record_provider(monkeypatch, failure, events, instances)

    @metric(
        code_revision="1",
        inputs={"lots": LocationDependency(provider=Provider.id)},
        name="root",
        description="Root",
        outputs={"answer": VALUES},
    )
    def root(
        lots: gpd.GeoDataFrame,
        context: RunContext,
        provider: Annotated[Provider, Resource("locations")],
    ) -> pd.DataFrame:
        assert provider is instances[-1]
        assert list(lots.location_id) == ["b", "a"]
        events.append("handler")
        context.report_progress(stage="handler", current=1)
        return pd.DataFrame({"value": [len(lots)]})

    runtime = _install(tmp_path, monkeypatch, root)
    if failure == "configuration":
        monkeypatch.setattr(Provider, "secret_names", ("credential",))
    entry = worker.RUNNER_REGISTRY["root"]
    assert "provider" in entry.implementations
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    inputs = {"lots": selection("b", "a", "b").model_dump(mode="json")}
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest.model_validate(
                {
                    "metric": "root",
                    "inputs": {k: v for k, v in inputs.items() if k != "parameters"},
                    **(
                        {"parameters": inputs["parameters"]}
                        if "parameters" in inputs
                        else {}
                    ),
                }
            ),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: native.id,
        )
    )
    assert events == []
    (envelope,) = enqueue.call_args.args
    assert submitted_input(envelope) == submitted_input(envelope) == inputs
    native.args = (envelope.model_dump(mode="json"),)
    runner = LocalRunner.from_definitions(
        {name: binding.definition for name, binding in entry.implementations.items()}
    )
    try:
        if failure:
            with pytest.raises(GraphExecutionError) as queued_error:
                run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
            _assert_failure_diagnostics(
                native.meta["failure"]["error"], failure, events
            )
            with pytest.raises(GraphExecutionError) as local_error:
                runner.run(
                    "root",
                    inputs=inputs,
                )
            assert type(queued_error.value.__cause__) is type(
                local_error.value.__cause__
            )
        else:
            result = run_and_read(
                worker.run_metric_task, envelope.model_dump(mode="json")
            )
            assert result["outputs"] == {"answer": [{"value": 2}]}
            assert "provenance" not in result
            assert submitted_input(envelope) == inputs
            assert native.meta["progress"]["stage"] == "handler"
            assert events[0] == "setup"
            assert events[1][0] == "resolve"
            assert set(events[1][1]) == {"b", "a"}
            assert len(events[1][1]) == 2
            assert events[2:] == ["handler", "close"]
            assert runner.run(
                "root",
                inputs=inputs,
            )["answer"].to_dict("records") == [{"value": 2}]
    finally:
        worker.RUNNER_REGISTRY.clear()
        worker.set_runner_temp_base(None)


def _assert_failure_diagnostics(
    diagnostics: dict[str, object], failure: str, events: list
) -> None:
    assert diagnostics["node"] == "root"
    assert diagnostics["dependency_path"] == ["root"]
    assert "secret credential" not in str(diagnostics)
    if failure != "close":
        assert diagnostics["path"] == (None if failure == "configuration" else "lots")
        assert diagnostics["type"] == (
            "resource_configuration"
            if failure == "configuration"
            else "location_resolution"
        )
        assert "handler" not in events
    if failure == "configuration":
        assert events == []
    else:
        assert events[-1] == "close"


def _record_provider(
    monkeypatch: pytest.MonkeyPatch, failure: str | None, events: list, instances: list
) -> None:
    diagnostic = "secret credential"

    def setup(provider: Provider) -> None:
        instances.append(provider)
        events.append("setup")
        if failure == "setup":
            raise RuntimeError(diagnostic)

    def resolve(provider: Provider, ids: list[str]) -> gpd.GeoDataFrame:
        assert provider is instances[-1]
        events.append(("resolve", ids))
        if failure == "unknown":
            raise UnresolvedLocationIDsError(provider.id, "record", ids)
        if failure == "resolve":
            raise RuntimeError(diagnostic)
        return frame_features(
            ["unexpected"] if failure == "invalid" else list(reversed(ids))
        )

    def close(provider: Provider) -> None:
        assert provider is instances[-1]
        events.append("close")
        if failure == "close":
            raise RuntimeError(diagnostic)

    monkeypatch.setattr(Provider, "setup", setup)
    monkeypatch.setattr(Provider, "resolve", resolve)
    monkeypatch.setattr(Provider, "close", close)


def _install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: Callable[..., object]
) -> ApplicationRuntime:
    definitions = {
        "metric": PluginDefinition(metrics=[root], resources={"locations": Provider}),
        "provider": PluginDefinition(resources={"locations": Provider}),
    }
    plugins = [
        InstalledPlugin(
            name,
            definition,
            dict.fromkeys(definition.metric_names, "heavy"),
        )
        for name, definition in definitions.items()
    ]
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(
        loading,
        "load_plugin_definition",
        lambda ref: definitions[ref.split(":")[0]],
    )
    config = load_test_config(tmp_path)
    worker.refresh_runner_registry("heavy", config=config)
    worker.set_runner_temp_base(tmp_path / "scratch")
    return make_runtime(config)
