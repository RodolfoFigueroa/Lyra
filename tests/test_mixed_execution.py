"""Mixed invocation execution with in-process handlers and fake services."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk import (
    Dependency,
    Input,
    LocalRunner,
    LocationContext,
    LocationDependency,
    Locations,
    MetricParameters,
    PluginDefinition,
    RunContext,
    location_operation,
    metric,
)
from lyra.sdk.arguments import prepare_dependency
from lyra.sdk.dependencies import LiteralArgument
from lyra.sdk.errors import LocationAcquisitionError
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import build_graph
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.results import serialize_outputs
from pydantic import Field
from shapely.geometry import Point

from lyra_app import job_submission, worker
from tests.config_helpers import worker_credentials
from tests.location_helpers import output_schema
from tests.test_location_resolution import Provider, selection
from tests.test_location_schema import contract, declaration, locations
from tests.test_worker_graph_integration import (
    TABLE,
    VALUES,
    install_definitions,
    native_job,
)


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Count")


@pytest.fixture(autouse=True)
def worker_cleanup() -> Iterator[None]:
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


@pytest.mark.parametrize("persistent", [False, True])
def test_nested_ownership_table_conversion_and_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, persistent: bool
) -> None:
    trace = []
    preparation = Mock(wraps=prepare_dependency)
    monkeypatch.setattr("lyra.sdk.execution.prepare_dependency", preparation)

    @metric(name="records", description="Records", outputs={"records": VALUES})
    def records(parameters: Parameters) -> pd.DataFrame:
        trace.append(("records", parameters.count))
        return pd.DataFrame({"value": range(parameters.count)})

    @location_operation(
        name="construct",
        description="Construct",
        output=contract(declaration()),
        inputs={"table": Dependency("records")},
    )
    def construct(parameters: Parameters, table: pd.DataFrame) -> Locations:
        trace.append(("construct", parameters.count))
        frame = gpd.GeoDataFrame(
            {"key": [str(v) for v in table.value]},
            geometry=[Point(1, 2)] * len(table),
            crs=4326,
        )
        return Locations.from_geodataframe(
            frame, collection="example:places", unit_type="point", id_column="key"
        )

    @location_operation(
        name="select", description="Select", output=contract(declaration())
    )
    def select(source: Locations, parameters: Parameters) -> Locations:
        trace.append(("select", parameters.count))
        assert len(source.ids) in {3, 4}  # References belong to root, not select.
        return source

    @metric(
        name="consume",
        description="Consume",
        outputs={"consume": VALUES},
        inputs={"support": Dependency("records")},
    )
    def consume(
        location: Locations, parameters: Parameters, support: pd.DataFrame
    ) -> pd.DataFrame:
        trace.append(("consume", parameters.count))
        assert len(support) == 2  # Own dependencies use consume's inputs.
        return pd.DataFrame({"value": [len(location.ids)]})

    binding = LocationDependency(
        "select",
        parameters={"count": 9},
        locations={
            "source": LocationDependency(
                "construct", parameters={"count": Input("parameters.count")}
            )
        },
    )

    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "a": Dependency(
                "consume", parameters={"count": 2}, locations={"location": binding}
            ),
            "b": Dependency(
                "consume",
                parameters={"count": 2},
                locations={
                    "location": LocationDependency(
                        "construct", parameters={"count": Input("parameters.count")}
                    )
                },
            ),
        },
    )
    def root(parameters: Parameters, a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        assert parameters.count == a.value.iloc[0]
        assert a is b
        return a

    definitions = {
        "root": PluginDefinition(metrics=[root]),
        "tables": PluginDefinition(metrics=[consume, records]),
        "operations": PluginDefinition(location_operations=[select, construct]),
    }
    _, runtime = install_definitions(
        tmp_path,
        monkeypatch,
        definitions,
        cache_dir=tmp_path / "worker-cache" if persistent else None,
    )
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert runtime.catalog.get_metric_entry("construct") is None
    assert set(definitions["root"].describe("root").request_schema["properties"]) == {
        "parameters"
    }
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    for path in ("local", "queued"):
        for iteration, count in enumerate((3, 3, 4)):
            trace.clear()
            preparation.reset_mock()
            inputs = {"parameters": {"count": count}}
            if path == "local":
                runner = LocalRunner.from_definitions(
                    definitions,
                    cache_dir=tmp_path / "local-cache" if persistent else None,
                )
                result = serialize_outputs("root", runner.run("root", inputs))
            else:
                worker.refresh_runner_registry(
                    "heavy", config=runtime.config, credentials=worker_credentials()
                )
                native = native_job(monkeypatch)
                asyncio.run(
                    job_submission.submit_job(
                        JobCreateRequest(metric="root", input=inputs),
                        catalog=runtime.catalog,
                        jobs=runtime.jobs,
                        job_id_factory=lambda native=native: native.id,
                    )
                )
                assert trace == []
                envelope, provenance, _ = enqueue.call_args.args
                assert provenance.input == inputs
                native.meta["provenance"] = provenance.model_dump(mode="json")
                result = worker.run_metric_task(envelope.model_dump(mode="json"))[
                    "outputs"
                ]
            assert result == {"answer": [{"value": count}]}
            if not persistent:
                assert preparation.call_count == 6
            assert trace == (
                []
                if persistent and iteration == 1
                else [
                    ("records", count),
                    ("construct", count),
                    ("select", 9),
                    *([] if persistent and iteration == 2 else [("records", 2)]),
                    ("consume", 2),
                ]
            )


@pytest.mark.parametrize("failure", [None, "handler", "result", "resolution"])
def test_shared_provider_local_and_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events = []
    contexts: list[RunContext] = []
    broken = failure
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))

    @location_operation(
        name="discover", description="Discover", output=output_schema(original=str)
    )
    def discover(source: Locations, context: LocationContext) -> object:
        contexts.append(context)
        events.append("discover")
        if broken == "handler":
            message = "discovery failed"
            raise RuntimeError(message)
        if broken == "result":
            return ["a"]
        return context.resolve_locations(
            source.collection,
            "missing" if broken == "resolution" else source.unit_type,
            ["b", "a", "b"],
            input_name="discovered",
        )

    @metric(name="consume", description="Consume", outputs={"consume": VALUES})
    def consume(location: Locations) -> pd.DataFrame:
        assert location.ids == ["b", "a"]
        return pd.DataFrame({"value": [len(location.ids)]})

    @metric(
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "data": Dependency(
                "consume",
                locations={
                    "location": LocationDependency(
                        "discover", locations={"source": Input("source")}
                    )
                },
            )
        },
    )
    def root(source: Locations, data: pd.DataFrame) -> pd.DataFrame:
        assert source.ids == ["initial"]
        return data

    definitions = {
        "metrics": PluginDefinition(metrics=[root, consume]),
        "operations": PluginDefinition(location_operations=[discover]),
        "provider": PluginDefinition(
            metrics=[], resources={"locations": Provider}, location_provider="locations"
        ),
    }
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    assert runtime.catalog.location_provider is not None
    assert runtime.catalog.location_provider.distribution == "provider"
    assert runtime.catalog.get_metric_entry("discover") is None
    assert set(
        definitions["metrics"].describe("root").request_schema["properties"]
    ) == {"source"}
    native = native_job(monkeypatch)
    inputs = {"source": selection("initial", "initial").model_dump(mode="json")}
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda native=native: native.id,
        )
    )
    assert events == []
    envelope, provenance, _ = enqueue.call_args.args
    native.meta["provenance"] = provenance.model_dump(mode="json")
    local = LocalRunner.from_definitions(definitions)
    for run in (
        lambda: local.run("root", inputs),
        lambda: worker.run_metric_task(envelope.model_dump(mode="json")),
    ):
        events.clear()
        if failure:
            with pytest.raises(GraphExecutionError) as error:
                run()
            assert (error.value.node, error.value.kind) == (
                "discover",
                "location_operation",
            )
            assert error.value.path == ("root", "consume", "discover")
            assert error.value.receiving_input == "inputs.data.locations.location"
            if failure == "resolution":
                assert "discovered" in str(error.value)
        else:
            result = run()
            assert "answer" in result or result["outputs"] == {"answer": [{"value": 2}]}
        assert events == ["setup", "discover", "close"]
    broken = None
    assert local.run("root", inputs)["answer"].value.tolist() == [2]
    assert not contexts[0].temp_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_operation_parameters_content_and_run_owned_results(*, empty: bool) -> None:
    calls = []

    @location_operation(name="make", description="Make", output=contract(declaration()))
    def make(parameters: Parameters, context: LocationContext) -> Locations:
        assert callable(context.resolve_locations)
        calls.append(parameters.count)
        value = locations() if empty else locations({})
        return value.model_copy(
            update={"collection": f"example:places{parameters.count}"}
        )

    @metric(name="consume", description="Consume", outputs={"consume": TABLE})
    def consume(location: Locations) -> pd.DataFrame:
        calls.append(location.collection)
        assert len(location.ids) == (0 if empty else 1)
        return pd.DataFrame()

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            name: Dependency(
                "consume",
                locations={
                    "location": LocationDependency("make", parameters={"count": count})
                },
            )
            for name, count in [("a", 1), ("b", 1), ("c", 2)]
        },
    )
    def root(a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
        assert a is b
        assert a is not c
        return a

    plugin = PluginDefinition(metrics=[root, consume], location_operations=[make])
    executor = GraphExecutor(
        build_graph({"mixed": plugin.node_manifests}),
        {"mixed": plugin},
        lambda node: Mock(spec=RunContext, node=node),
    )
    for _ in range(2):
        calls.clear()
        executor.execute(plugin.prepare_invocation("root", {}))
        assert calls == [1, "example:places1", 2, "example:places2"]
    with pytest.raises(GraphExecutionError, match="roots must be metrics"):
        executor.execute(plugin.prepare_invocation("make", {"parameters": {}}))


def test_preparation_failure_and_next_run_start_fresh() -> None:
    calls = []
    broken = True

    @location_operation(name="make", description="Make", output=contract(declaration()))
    def make(parameters: Parameters) -> Locations:
        assert parameters.count == 2
        calls.append("make")
        return locations({})

    @metric(name="consume", description="Consume", outputs={"consume": TABLE})
    def consume(location: Locations, parameters: Parameters) -> pd.DataFrame:
        assert location.ids == ["0"]
        calls.append("consume")
        assert parameters.count == 2
        return pd.DataFrame()

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            name: Dependency(
                "consume",
                parameters={"count": 2},
                locations={"location": LocationDependency("make")},
            )
            for name in ("first", "second")
        },
    )
    def root(first: pd.DataFrame, second: pd.DataFrame) -> pd.DataFrame:
        assert first is second
        return first

    plugin = PluginDefinition(metrics=[root, consume], location_operations=[make])
    graph = build_graph({"mixed": plugin.node_manifests})
    executor = GraphExecutor(graph, {"mixed": plugin}, Mock())
    for _ in range(2):
        calls.clear()
        graph.dependencies["root"]["second"].dependency.parameters["count"] = (
            LiteralArgument(value="invalid" if broken else 2)
        )
        if broken:
            with pytest.raises(GraphExecutionError) as error:
                executor.execute(plugin.prepare_invocation("root", {}))
            assert error.value.node == "consume"
            assert error.value.path == ("root", "consume")
            assert error.value.receiving_input == "inputs.second"
        else:
            executor.execute(plugin.prepare_invocation("root", {}))
        assert calls == ["make", "consume"]
        broken = False


def test_worker_retains_invocation_context_without_acquisition_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = SimpleNamespace(id="job", worker_name="worker", meta={}, save_meta=Mock())
    monkeypatch.setattr(worker, "get_current_job", lambda: native)
    secret = RuntimeError("password=private-token")
    acquisition = LocationAcquisitionError(
        "Provider acquisition failed", input_name="discovered"
    )
    acquisition.__cause__ = secret
    error = GraphExecutionError(
        "discover",
        ("root", "consume", "discover"),
        acquisition,
        kind="location_operation",
        receiving_input="inputs.data.locations.location",
    )
    error.__cause__ = acquisition
    monkeypatch.setattr(worker, "execute_job", Mock(side_effect=error))
    with pytest.raises(GraphExecutionError):
        worker.run_metric_task({})
    failure = native.meta["failure"]["error"]
    assert failure["node"] == "discover"
    assert failure["node_kind"] == "location_operation"
    assert failure["receiving_input"] == "inputs.data.locations.location"
    assert failure["path"] == "discovered"
    assert failure["dependency_path"] == ["root", "consume", "discover"]
    assert "private-token" not in str(failure)
    assert error.__cause__.__cause__ is secret


def test_empty_internal_resolution_never_acquires_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    for method in ("__init__", "setup", "resolve", "close"):
        monkeypatch.setattr(Provider, method, Mock(side_effect=AssertionError(method)))

    @location_operation(
        name="empty", description="Empty selection", output=output_schema(original=str)
    )
    def empty(context: LocationContext) -> Locations:
        value = context.resolve_locations("test:roads", "road", [], input_name="roads")
        seen.append(value)
        return value

    @metric(name="consume", description="Consume empty", outputs={"count": VALUES})
    def consume(roads: Locations) -> pd.DataFrame:
        assert roads.ids == []
        assert (roads.collection, roads.unit_type) == ("test:roads", "road")
        assert roads.crs.properties.name == "EPSG:27700"
        return pd.DataFrame({"value": [len(roads.ids)]})

    @metric(
        name="root",
        description="Internal empty lookup",
        outputs={"answer": VALUES},
        inputs={
            "data": Dependency(
                "count", locations={"roads": LocationDependency("empty")}
            )
        },
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    definitions = {
        "metrics": PluginDefinition(metrics=[root, consume]),
        "operations": PluginDefinition(location_operations=[empty]),
        "provider": PluginDefinition(
            metrics=[], resources={"locations": Provider}, location_provider="locations"
        ),
    }
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", input={}),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: native.id,
        )
    )
    assert seen == []
    envelope, provenance, _ = enqueue.call_args.args
    native.meta["provenance"] = provenance.model_dump(mode="json")
    local = LocalRunner.from_definitions(definitions).run("root", {})
    queued = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert (
        serialize_outputs("root", local)
        == queued["outputs"]
        == {"answer": [{"value": 0}]}
    )
    assert len(seen) == 2
    assert seen[0] == seen[1]
