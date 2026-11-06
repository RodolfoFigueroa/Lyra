"""Exercise canonical implementation discovery through real worker startup."""

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    LocalRunner,
    LocationDependency,
    Locations,
    PluginDefinition,
    computation,
    location_operation,
    metric,
    plugin_loader,
)
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.results import serialize_outputs

from lyra_app import job_submission, worker
from tests.location_helpers import node_contract, output_schema
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.test_location_resolution import Provider, selection
from tests.test_worker_graph_integration import (
    install_definitions,
    native_job,
)

pytestmark = pytest.mark.usefixtures("clean_state")


@pytest.fixture
def clean_state(tmp_path: Path) -> Iterator[None]:
    worker.set_runner_temp_base(tmp_path / "scratch")
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def definitions(
    calls: list[str], description: str = "Root"
) -> dict[str, PluginDefinition]:
    """Build split plugins with a two-hop route and nested dependencies."""
    schema = output_schema(original=str)
    source = next(c for c in Provider.contracts if c.family.unit_type == "lot")
    middle = node_contract("test:middle", "point", schema)
    target = next(c for c in Provider.contracts if c.family.unit_type == "block")
    table = pa.DataFrameSchema({})

    @location_operation(
        name="helper",
        description="Helper",
        locations={"source": source},
        output_contract=source,
        output=schema,
    )
    def helper(source: Locations) -> gpd.GeoDataFrame:
        calls.append("helper")
        return source.to_geodataframe()

    @metric(
        name="records",
        description="Records",
        private=True,
        locations={"source": source},
        outputs={"records": table},
    )
    def records(source: Locations) -> pd.DataFrame:
        calls.append("records")
        return pd.DataFrame(index=range(len(source)))

    def operation(name: str) -> Callable[[Locations, pd.DataFrame], gpd.GeoDataFrame]:
        @location_operation(
            name=name,
            description=name,
            canonical=True,
            locations={"source": source},
            output_contract=middle,
            output=schema,
            inputs={
                "data": Dependency(
                    "records",
                    producer="records",
                    locations={"source": LocationDependency("helper")},
                )
            },
        )
        def select(source: Locations, data: pd.DataFrame) -> gpd.GeoDataFrame:
            calls.append(name)
            assert len(data) == len(source)
            return source.to_geodataframe()

        return select

    first, alternate = operation("first"), operation("alternate")

    @location_operation(
        name="finish",
        description="Finish",
        canonical=True,
        locations={"source": middle},
        output_contract=target,
        output=schema,
    )
    def finish(source: Locations) -> gpd.GeoDataFrame:
        calls.append("finish")
        return source.to_geodataframe()

    @metric(
        name="root",
        description=description,
        locations={"left": target, "right": target},
        outputs={"answer": table},
    )
    def root(left: Locations, right: Locations) -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame(index=range(len(left) + len(right)))

    @metric(name="unrelated", description="Unrelated", outputs={"unused": table})
    def unrelated() -> pd.DataFrame:
        pytest.fail("Unrelated handler invoked")

    @location_operation(
        name="unused",
        description="Unused",
        locations={"source": source},
        output_contract=source,
        output=schema,
    )
    def unused(source: Locations) -> gpd.GeoDataFrame:
        pytest.fail(f"Unrelated operation invoked with {len(source)} locations")

    return {
        "metric": PluginDefinition(metrics=[root]),
        "first": PluginDefinition(location_operations=[first]),
        "alternate": PluginDefinition(location_operations=[alternate]),
        "finish": PluginDefinition(location_operations=[finish]),
        "records": PluginDefinition(metrics=[records]),
        "helper": PluginDefinition(location_operations=[helper]),
        "provider": PluginDefinition(
            resources={"locations": Provider},
            location_provider="locations",
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
    installed, runtime = install_definitions(tmp_path, monkeypatch, plugins)
    assert calls == []
    acquisition.assert_not_called()
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    expected_plugins = set(plugins) - {"unrelated", "unused"}
    assert set(worker.RUNNER_REGISTRY["root"].implementations) == expected_plugins
    inputs = dict.fromkeys(
        ["left", "right"], selection("a", "b", unit_type="block" if native else "lot")
    )
    expected = LocalRunner.from_definitions(plugins).run("root", inputs)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input=inputs),
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
        next(
            p.manifest for p in installed if p.distribution == "provider"
        ).route_preferences = plugins["provider"].route_preferences
    load = Mock(wraps=plugin_loader.load_plugin_definition)
    monkeypatch.setattr(plugin_loader, "load_plugin_definition", load)
    calls.clear()
    acquisition.reset_mock()
    worker.refresh_runner_registry("heavy", config=runtime.config)
    assert {
        call.args[0].split(":", maxsplit=1)[0] for call in load.call_args_list
    } == expected_plugins
    assert load.call_count == len(expected_plugins)
    assert calls == []
    acquisition.assert_not_called()
    job = native_job(monkeypatch)
    job.args = (envelope.model_dump(mode="json"),)
    result = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert result["outputs"] == serialize_outputs("root", expected)
    assert "alternate" not in calls
    assert set(calls) == (
        {"root"} if native else {"helper", "records", "first", "finish", "root"}
    )
    assert envelope.model_dump(mode="json")["accepted"]["plan"]["inputs"][0]["binding"][
        "operations"
    ] == ([] if native else ["first", "finish"])


@pytest.mark.parametrize("issue", ["missing", "stale"])
def test_required_operation_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue: str,
) -> None:
    installed, runtime = install_definitions(tmp_path, monkeypatch, definitions([]))
    if issue == "stale":
        next(p.manifest for p in installed if p.distribution == "first").nodes[
            0
        ].description = "stale"
        match = "build-manifest"
    else:
        original = plugin_loader.load_plugin_definition

        def load(ref: str) -> PluginDefinition:
            if ref.startswith("first:"):
                message = "required operation unavailable"
                raise RuntimeError(message)
            return original(ref)

        monkeypatch.setattr(plugin_loader, "load_plugin_definition", load)
        match = "required operation unavailable"
    with pytest.raises(RuntimeError, match=match):
        worker.load_runner_metric_entries("heavy", config=runtime.config)


def test_dependency_closure() -> None:
    runner = LocalRunner.from_definitions(definitions([]))
    graph = runner.catalog.graph
    assert graph.dependency_closure([]) == ()
    assert graph.dependency_closure(["first", "first"]) == (
        "first",
        "helper",
        "records",
    )
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
    versions: dict[str, str] = {}
    monkeypatch.setattr(computation, "installed_version", versions.get)
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
        versions[change] = "2"
    install_definitions(tmp_path, monkeypatch, plugins)
    native = native_job(monkeypatch)
    native.args = (envelope.model_dump(mode="json"),)
    acquire = Mock(wraps=Provider.setup)
    monkeypatch.setattr(Provider, "setup", acquire)
    if change in {"first", "helper", "provider"}:
        with pytest.raises(worker.BoundPlanError, match="identity differs"):
            run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert calls == []
        acquire.assert_not_called()
    else:
        # The fixture provider has no service connections.
        monkeypatch.setattr(Provider, "setup", lambda _self: None)
        run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
        assert set(calls) == {"helper", "records", "first", "finish", "root"}
