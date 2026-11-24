"""Exercise canonical implementation discovery through real worker startup."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.dependencies import Input
from lyra.contracts.jobs import JobCreateRequest
from lyra.data.results import serialize_outputs
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.server import job_submission, worker
from test_support.location_resolution import Blocks, Provider, selection
from test_support.table_fixtures import value_frame, value_schema
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.worker_graph_integration_helpers import (
    install_definitions,
    native_job,
)

pytestmark = pytest.mark.usefixtures("worker_state")


def definitions(
    calls: list[str], description: str = "Root", revisions: dict[str, str] | None = None
) -> dict[str, PluginDefinition]:
    """Build split plugins with a two-hop route and nested dependencies."""
    revisions = revisions or {}

    class Middle(Provider):
        id = "test_middle"

    class Source(Provider):
        code_revision = revisions.get("provider")

    table = value_schema()

    @location_operation(
        code_revision=revisions.get("helper", "1"),
        name="helper",
        description="Helper",
        inputs={"source": LocationDependency(provider=Blocks.id)},
        target_provider=Blocks.id,
    )
    def helper(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append("helper")
        return {key: (key,) for key in source.location_id}

    @metric(
        code_revision=revisions.get("records", "1"),
        name="records",
        description="Records",
        private=True,
        inputs={"source": LocationDependency(provider=Blocks.id)},
        outputs={"records": table},
    )
    def records(source: gpd.GeoDataFrame) -> pd.DataFrame:
        calls.append("records")
        return value_frame(len(source))

    def operation(
        name: str,
    ) -> Callable[[gpd.GeoDataFrame], dict[str, tuple[str, ...]]]:
        @location_operation(
            code_revision=revisions.get(name, "1"),
            name=name,
            description=name,
            canonical=True,
            inputs={"source": LocationDependency(provider=Provider.id)},
            target_provider=Middle.id,
        )
        def select(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
            calls.append(name)
            return {key: (key,) for key in source.location_id}

        return select

    first, alternate = operation("first"), operation("alternate")

    @location_operation(
        code_revision=revisions.get("finish", "1"),
        name="finish",
        description="Finish",
        canonical=True,
        inputs={"source": LocationDependency(provider=Middle.id)},
        target_provider=Blocks.id,
    )
    def finish(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append("finish")
        return {key: (key,) for key in source.location_id}

    @metric(
        code_revision=revisions.get("root", "1"),
        name="root",
        description=description,
        inputs={
            "left": LocationDependency(provider=Blocks.id),
            "right": LocationDependency(provider=Blocks.id),
            "data": Dependency("records", producer="records").bind(
                source=LocationDependency("helper").bind(source=Input("left"))
            ),
        },
        outputs={"answer": table},
    )
    def root(
        left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, data: pd.DataFrame
    ) -> pd.DataFrame:
        assert len(data) == len(left)
        calls.append("root")
        return value_frame(len(left) + len(right))

    @metric(
        code_revision=revisions.get("unrelated", "1"),
        name="unrelated",
        description="Unrelated",
        outputs={"unused": table},
    )
    def unrelated() -> pd.DataFrame:
        pytest.fail("Unrelated handler invoked")

    @location_operation(
        code_revision=revisions.get("unused", "1"),
        name="unused",
        description="Unused",
        inputs={"source": LocationDependency(provider=Blocks.id)},
        target_provider=Blocks.id,
    )
    def unused(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        pytest.fail(f"Unrelated operation invoked with {len(source)} locations")

    return {
        "metric": PluginDefinition(metrics=[root]),
        "first": PluginDefinition(location_operations=[first]),
        "alternate": PluginDefinition(location_operations=[alternate]),
        "finish": PluginDefinition(location_operations=[finish]),
        "records": PluginDefinition(metrics=[records]),
        "helper": PluginDefinition(location_operations=[helper]),
        "provider": PluginDefinition(
            resources={"locations": Source, "middle": Middle, "blocks": Blocks},
            route_preferences=[(first,), (first, finish)],
        ),
        "unrelated": PluginDefinition(metrics=[unrelated]),
        "unused": PluginDefinition(location_operations=[unused]),
    }


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("change_preference", [False, True])
def test_split_route_loading_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    native: bool,
    change_preference: bool,
) -> None:
    calls: list[str] = []
    plugins = definitions(calls)
    acquisition = Mock()
    monkeypatch.setattr(Provider, "setup", acquisition)
    _, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    assert calls == []
    acquisition.assert_not_called()
    inputs = dict.fromkeys(
        ["left", "right"],
        selection("a", "b", provider=Blocks.id if native else Provider.id),
    )
    expected = (
        LocalRunner.from_definitions(plugins)
        .run(
            "root",
            inputs={k: v.model_dump(mode="json") for k, v in inputs.items()},
        )
        .tables
    )
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest.model_validate(
                {
                    "metric": "root",
                    "inputs": {k: v.model_dump(mode="json") for k, v in inputs.items()},
                    **(
                        {"parameters": inputs["parameters"]}
                        if "parameters" in inputs
                        else {}
                    ),
                }
            ),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: "graph-job",
        )
    )
    (envelope,) = enqueue.call_args.args
    if change_preference:
        plugins["provider"].route_preferences = (
            ("alternate",),
            ("alternate", "finish"),
        )
        runtime.config = runtime.config.model_copy(
            update={"build_revision": "new-routing-build"}
        )
    calls.clear()
    acquisition.reset_mock()
    worker.initialize_worker("heavy", config=runtime.config)
    assert calls == []
    acquisition.assert_not_called()
    job = native_job(monkeypatch)
    job.args = (envelope.model_dump(mode="json"),)
    if change_preference:
        with pytest.raises(worker.DeploymentChangedError, match="deployment_changed"):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert calls == []
        acquisition.assert_not_called()
        return
    result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert result["outputs"] == serialize_outputs("root", expected)
    assert "alternate" not in calls
    assert set(calls) == (
        {"helper", "records", "root"}
        if native
        else {"helper", "records", "first", "finish", "root"}
    )
    assert envelope.accepted.plan.request.inputs == inputs


def test_dependency_closure() -> None:
    runner = LocalRunner.from_definitions(definitions([]))
    graph = runner.catalog.graph
    assert graph.dependency_closure([]) == ()
    assert graph.dependency_closure(["root", "root"]) == (
        "helper",
        "records",
        "root",
    )
    assert graph.dependency_closure(["first", "first"]) == ("first",)
    assert graph.dependency_closure(["root", "first"]) == graph.dependency_closure(
        ["first", "root"]
    )
    with pytest.raises(KeyError):
        graph.dependency_closure(["missing"])


@pytest.mark.parametrize(
    "change", ["editorial", "unrelated", "alternate", "first", "helper", "provider"]
)
def test_accepted_route_identity_across_worker_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    calls: list[str] = []
    plugins = definitions(calls)
    _, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    envelope = accepted_envelope(
        job_id="graph-job",
        metric="root",
        inputs=dict.fromkeys(["left", "right"], selection("a").model_dump(mode="json")),
        catalog=runtime.catalog.compiled,
    )
    if change == "editorial":
        plugins = definitions(calls, description="Updated explanation")
    else:
        plugins = definitions(calls, revisions={change: "2"})
    install_definitions(tmp_path, monkeypatch, plugins)
    native = native_job(monkeypatch)
    native.args = (envelope.model_dump(mode="json"),)
    acquire = Mock(wraps=Provider.setup)
    monkeypatch.setattr(Provider, "setup", acquire)
    if change in {"first", "helper", "provider"}:
        if change == "provider":
            expected = worker.BoundPlanError
            failure = "bound_plan"
        else:
            current = worker.require_worker_config()
            worker.initialize_worker(
                "heavy",
                config=current.model_copy(update={"build_revision": "replacement"}),
            )
            expected = worker.DeploymentChangedError
            failure = "deployment_changed"
        with pytest.raises(expected):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert calls == []
        acquire.assert_not_called()
        assert native.meta["failure"]["error"]["type"] == failure
    else:
        # The fixture provider has no service connections.
        monkeypatch.setattr(Provider, "setup", lambda _self: None)
        run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert set(calls) == {"helper", "records", "first", "finish", "root"}
