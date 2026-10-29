"""Workers resolve selections with provider-only plugins in one managed scope."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import (
    LocalRunner,
    Locations,
    PluginDefinition,
    Resource,
    RunContext,
    metric,
    plugin_loader,
)
from lyra.sdk.errors import UnresolvedLocationIDsError
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import PluginInfo

from lyra_app import job_submission, registry, worker
from lyra_app.plugins import InstalledPlugin
from lyra_app.runtime import ApplicationRuntime
from tests.config_helpers import load_test_config, worker_credentials
from tests.runtime_helpers import make_runtime
from tests.test_location_resolution import Provider, features, selection
from tests.test_worker_graph_integration import VALUES, native_job


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

    @metric(name="root", description="Root", outputs={"answer": VALUES})
    def root(
        lots: Locations,
        context: RunContext,
        provider: Annotated[Provider, Resource("locations")],
    ) -> pd.DataFrame:
        assert provider is instances[-1]
        assert [feature.id for feature in lots.features] == ["b", "a"]
        events.append("handler")
        context.report_progress(stage="handler", current=1)
        return pd.DataFrame({"value": [len(lots.features)]})

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
            JobCreateRequest(metric="root", input=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: native.id,
        )
    )
    assert events == []
    envelope, provenance, _ = enqueue.call_args.args
    assert envelope.input == provenance.input == inputs
    native.meta["provenance"] = provenance.model_dump(mode="json")
    runner = LocalRunner.from_definitions(entry.implementations)
    try:
        if failure:
            with pytest.raises(GraphExecutionError) as queued_error:
                worker.run_metric_task(envelope.model_dump(mode="json"))
            _assert_failure_diagnostics(
                native.meta["failure"]["error"], failure, events
            )
            with pytest.raises(GraphExecutionError) as local_error:
                runner.run("root", inputs)
            assert type(queued_error.value.__cause__) is type(
                local_error.value.__cause__
            )
        else:
            result = worker.run_metric_task(envelope.model_dump(mode="json"))
            assert result["outputs"] == {"answer": [{"value": 2}]}
            assert isinstance(result["provenance"], dict)
            assert result["provenance"]["input"] == inputs
            assert native.meta["progress"]["stage"] == "handler"
            assert events == ["setup", ("resolve", ["b", "a"]), "handler", "close"]
            assert runner.run("root", inputs)["answer"].to_dict("records") == [
                {"value": 2}
            ]
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
        assert diagnostics["path"] == "lots"
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

    def resolve(
        provider: Provider, collection: str, unit_type: str, ids: list[str]
    ) -> Locations:
        assert provider is instances[-1]
        events.append(("resolve", ids))
        if failure == "unknown":
            raise UnresolvedLocationIDsError(collection, unit_type, ids)
        if failure == "resolve":
            raise RuntimeError(diagnostic)
        return features(["unexpected"] if failure == "invalid" else list(reversed(ids)))

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
        "provider": PluginDefinition(
            metrics=[], resources={"locations": Provider}, location_provider="locations"
        ),
    }
    plugins = [
        InstalledPlugin(
            name,
            definition.manifest(
                plugin=PluginInfo(name=name, version="1"), factory=f"{name}:create"
            ),
            dict.fromkeys(definition.metric_names, "heavy"),
        )
        for name, definition in definitions.items()
    ]
    monkeypatch.setattr(worker, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(registry, "load_installed_plugins", lambda _: plugins)
    monkeypatch.setattr(
        plugin_loader,
        "load_plugin_definition",
        lambda ref: definitions[ref.split(":")[0]],
    )
    config = load_test_config(tmp_path)
    worker.refresh_runner_registry(
        "heavy", config=config, credentials=worker_credentials()
    )
    worker.set_runner_temp_base(tmp_path / "scratch")
    return make_runtime(config)
