"""Mixed invocation execution with in-process handlers and fake services."""

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import LocationAcquisitionError
from lyra.contracts.jobs import JobCreateRequest
from lyra.data.locations import Locations
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition as NativePluginDefinition
from lyra.sdk.plugin import location_operation as native_operation
from lyra.sdk.plugin import metric as native_metric
from lyra.server import job_submission, worker
from test_support.declarations import PluginDefinition, location_operation, metric
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    producer_result,
)
from test_support.location_resolution import Provider, Roads, selection
from test_support.location_schema import contract, declaration, locations
from tests.queue_helpers import run_and_read, submitted_input
from tests.worker_graph_integration_helpers import (
    TABLE,
    VALUES,
    install_definitions,
    native_job,
)


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Count")


pytestmark = pytest.mark.usefixtures("worker_cleanup")


@pytest.fixture
def worker_cleanup() -> Iterator[None]:
    yield
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


@pytest.mark.parametrize("persistent", [False, True])
def test_nested_ownership_table_conversion_and_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, persistent: bool
) -> None:
    trace = []

    @native_metric(
        code_revision="1",
        name="records",
        description="Records",
        outputs={"records": VALUES},
    )
    def records(parameters: Parameters) -> pd.DataFrame:
        trace.append(("records", parameters.count))
        return pd.DataFrame({"value": range(parameters.count)})

    @native_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target=Provider.id,
        name="construct",
        description="Select a parameterized prefix",
    )
    def construct(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> gpd.GeoDataFrame:
        trace.append(("construct", parameters.count))
        return source.iloc[: parameters.count].copy()

    @native_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target=Provider.id,
        name="select",
        description="Select supplied places",
    )
    def select(source: gpd.GeoDataFrame, *, parameters: Parameters) -> gpd.GeoDataFrame:
        trace.append(("select", parameters.count))
        assert len(source) in {3, 4}
        return source

    @native_metric(
        code_revision="1",
        name="consume",
        description="Consume",
        outputs={"consume": VALUES},
        inputs={
            "location": LocationDependency(provider=Provider.id),
            "support": Dependency("records", producer="records"),
        },
    )
    def consume(
        location: gpd.GeoDataFrame, parameters: Parameters, support: pd.DataFrame
    ) -> pd.DataFrame:
        trace.append(("consume", parameters.count))
        assert len(support) == 2
        return pd.DataFrame({"value": [len(location)]})

    binding = LocationDependency(
        "select",
        parameters={"count": 9},
    ).bind(
        source=LocationDependency(
            "construct", parameters={"count": Input("parameters.count")}
        ).bind(source=Input("source"))
    )

    @native_metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "source": LocationDependency(provider=Provider.id),
            "a": Dependency(
                "consume",
                parameters={"count": 2},
                producer="consume",
            ).bind(location=binding),
            "b": Dependency(
                "consume",
                parameters={"count": 2},
                producer="consume",
            ).bind(
                location=LocationDependency(
                    "construct", parameters={"count": Input("parameters.count")}
                ).bind(source=Input("source"))
            ),
        },
    )
    def root(
        source: gpd.GeoDataFrame,
        parameters: Parameters,
        a: pd.DataFrame,
        b: pd.DataFrame,
    ) -> pd.DataFrame:
        assert len(source) == 4
        assert parameters.count == a.value.iloc[0]
        assert a is b
        return a

    definitions = {
        "root": NativePluginDefinition(metrics=[root]),
        "tables": NativePluginDefinition(
            metrics=[consume, records], resources={"locations": Provider}
        ),
        "operations": NativePluginDefinition(location_operations=[select, construct]),
    }
    _, runtime = install_definitions(
        tmp_path,
        monkeypatch,
        definitions,
        artifact_dir=tmp_path / "worker-cache" if persistent else None,
    )
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert "construct" not in runtime.catalog.get_loaded_metric_names()
    assert set(
        runtime.catalog.compiled.graph.nodes["root"].node.request_schema["properties"]
    ) == {"parameters", "source"}
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    for path in ("local", "queued"):
        for iteration, count in enumerate((3, 3, 4)):
            trace.clear()
            inputs = {
                "parameters": {"count": count},
                "source": selection("0", "1", "2", "3").model_dump(mode="json"),
            }
            if path == "local":
                runner = LocalRunner.from_definitions(
                    definitions,
                    artifact_dir=tmp_path / "local-cache" if persistent else None,
                )
                result = serialize_outputs(
                    "root",
                    runner.run(
                        "root",
                        inputs={k: v for k, v in inputs.items() if k != "parameters"},
                        parameters=inputs.get("parameters"),
                    ),
                )
            else:
                worker.refresh_runner_registry("heavy", config=runtime.config)
                native = native_job(monkeypatch)
                asyncio.run(
                    job_submission.submit_job(
                        JobCreateRequest.model_validate(
                            {
                                "metric": "root",
                                "inputs": {
                                    k: v for k, v in inputs.items() if k != "parameters"
                                },
                                **(
                                    {"parameters": inputs["parameters"]}
                                    if "parameters" in inputs
                                    else {}
                                ),
                            }
                        ),
                        catalog=runtime.catalog,
                        jobs=runtime.jobs,
                        job_id_factory=lambda native=native: native.id,
                    )
                )
                assert trace == []
                (envelope,) = enqueue.call_args.args
                assert submitted_input(envelope) == inputs
                native.args = (envelope.model_dump(mode="json"),)
                result = run_and_read(
                    worker.run_metric_task, envelope.model_dump(mode="json")
                )["outputs"]
            assert result == {"answer": [{"value": count}]}
            cached = persistent or path == "queued"
            assert trace == (
                []
                if cached and iteration == 1
                else [
                    ("construct", count),
                    ("select", 9),
                    *([] if cached and iteration == 2 else [("records", 2)]),
                    ("consume", 2),
                ]
            )


def assert_mixed_outcome(run: Callable[[], dict], failure: str | None) -> None:
    if failure == "resolution":
        with pytest.raises(GraphExecutionError) as error:
            run()
        assert error.value.node == "root"
        assert isinstance(error.value.__cause__, LocationAcquisitionError)
    elif failure:
        with pytest.raises(GraphExecutionError) as error:
            run()
        assert (error.value.node, error.value.kind) == (
            "discover",
            "location_operation",
        )
        assert error.value.path == ("root", "consume", "discover")
        assert error.value.receiving_input == "inputs.data.locations.location"
    else:
        result = run()
        assert "answer" in result or result["outputs"] == {"answer": [{"value": 2}]}


@pytest.mark.parametrize("failure", [None, "handler", "result", "resolution"])
def test_shared_provider_local_and_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events = []
    contexts: list[RunContext] = []
    broken = failure
    original_resolve = Provider.resolve
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))

    def resolve(provider: Provider, ids: list[str]) -> gpd.GeoDataFrame:
        events.append("resolve")
        if broken == "resolution":
            message = "acquisition failed"
            raise RuntimeError(message)
        return original_resolve(provider, ids)

    monkeypatch.setattr(Provider, "resolve", resolve)

    @native_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target=Provider.id,
        name="discover",
        description="Select supplied records",
    )
    def discover(source: gpd.GeoDataFrame, context: RunContext) -> object:
        contexts.append(context)
        events.append("discover")
        if broken == "handler":
            message = "discovery failed"
            raise RuntimeError(message)
        if broken == "result":
            return ["a"]
        return source

    @native_metric(
        code_revision="1",
        inputs={"location": LocationDependency(provider=Provider.id)},
        name="consume",
        description="Consume",
        outputs={"consume": VALUES},
    )
    def consume(location: gpd.GeoDataFrame) -> pd.DataFrame:
        assert location.location_id.tolist() == ["b", "a"]
        return pd.DataFrame({"value": [len(location)]})

    @native_metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"answer": VALUES},
        inputs={
            "source": LocationDependency(provider=Provider.id),
            "data": Dependency("consume", producer="consume").bind(
                location=LocationDependency("discover").bind(source=Input("source"))
            ),
        },
    )
    def root(source: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert source.location_id.tolist() == ["b", "a"]
        return data

    definitions = {
        "metrics": NativePluginDefinition(metrics=[root, consume]),
        "operations": NativePluginDefinition(location_operations=[discover]),
        "provider": NativePluginDefinition(resources={"locations": Provider}),
    }
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    assert runtime.catalog.compiled.providers[Provider.id].distribution == "provider"
    assert "discover" not in runtime.catalog.get_loaded_metric_names()
    native = native_job(monkeypatch)
    inputs = {"source": selection("b", "a", "b").model_dump(mode="json")}
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: native.id,
        )
    )
    assert events == []
    (envelope,) = enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    local = LocalRunner.from_definitions(definitions)
    for run in (
        lambda: local.run("root", inputs=inputs),
        lambda: run_and_read(worker.run_metric_task, envelope.model_dump(mode="json")),
    ):
        events.clear()
        assert_mixed_outcome(run, failure)
        assert events == [
            "setup",
            "resolve",
            *([] if failure == "resolution" else ["discover"]),
            "close",
        ]
    broken = None
    assert local.run("root", inputs=inputs)["answer"].value.tolist() == [2]
    assert not contexts[0].temp_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_operation_parameters_content_and_run_owned_results(*, empty: bool) -> None:
    calls = []

    @location_operation(
        code_revision="1",
        output_contract=node_contract(
            "example:places1", "point", contract(declaration())
        ),
        name="make",
        description="Make",
        output=contract(declaration()),
    )
    def make(*, parameters: Parameters, context: RunContext) -> gpd.GeoDataFrame:
        context.report_progress(stage="construct", current=parameters.count)
        calls.append(parameters.count)
        value = locations() if empty else locations({})
        return producer_result(
            fixture_locations(
                value.to_dict() | {"collection": f"example:places{parameters.count}"}
            ),
            contract(declaration()),
        )

    @location_operation(
        code_revision="1",
        output_contract=node_contract(
            "example:places2", "point", contract(declaration())
        ),
        name="make_two",
        description="Second scope",
        output=contract(declaration()),
    )
    def make_two(*, parameters: Parameters, context: RunContext) -> gpd.GeoDataFrame:
        return make(parameters=parameters, context=context)

    def consumer(name: str, count: int) -> Callable[[Locations], pd.DataFrame]:
        @metric(
            code_revision="1",
            locations={
                "location": node_contract(
                    f"example:places{count}", "point", contract(declaration())
                )
            },
            name=name,
            description="Consume",
            outputs={name: TABLE},
        )
        def consume(location: Locations) -> pd.DataFrame:
            calls.append(location.collection)
            assert len(location.ids) == (0 if empty else 1)
            return pd.DataFrame()

        return consume

    consume = consumer("consume", 1)
    consume_two = consumer("consume_two", 2)

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            name: Dependency(
                "consume" if count == 1 else "consume_two",
                producer="consume" if count == 1 else "consume_two",
            ).bind(
                location=LocationDependency(
                    "make" if count == 1 else "make_two",
                    parameters={"count": count},
                )
            )
            for name, count in [("a", 1), ("b", 1), ("c", 2)]
        },
    )
    def root(a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
        assert a is b
        assert a is not c
        return a

    plugin = PluginDefinition(
        metrics=[root, consume, consume_two], location_operations=[make, make_two]
    )
    executor = GraphExecutor(
        LocalRunner.from_definitions({"mixed": plugin}).catalog,
        {"mixed": BoundPlugin(plugin)},
        lambda node: Mock(spec=RunContext, node=node),
    )
    for _ in range(2):
        calls.clear()
        executor.execute(
            executor.plugins["mixed"].prepare_resolved_invocation("root", {})
        )
        assert calls == [1, "example:places1", 2, "example:places2"]
    with pytest.raises(GraphExecutionError, match="roots must be metrics"):
        executor.execute(
            executor.plugins["mixed"].prepare_resolved_invocation(
                "make", {"parameters": {}}
            )
        )


def test_preparation_failure_and_next_run_start_fresh() -> None:
    calls = []
    broken = True

    @location_operation(
        code_revision="1",
        output_contract=node_contract(
            "example:places", "point", contract(declaration())
        ),
        name="make",
        description="Make",
        output=contract(declaration()),
    )
    def make(*, parameters: Parameters) -> gpd.GeoDataFrame:
        assert parameters.count == 2
        calls.append("make")
        return producer_result(locations({}), contract(declaration()))

    @metric(
        code_revision="1",
        locations={
            "location": node_contract(
                "example:places", "point", contract(declaration())
            )
        },
        name="consume",
        description="Consume",
        outputs={"consume": TABLE},
    )
    def consume(location: Locations, parameters: Parameters) -> pd.DataFrame:
        assert location.ids == ["0"]
        calls.append("consume")
        assert parameters.count == 2
        return pd.DataFrame()

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={
            name: Dependency(
                "consume",
                parameters={"count": 2},
                producer="consume",
            ).bind(location=LocationDependency("make"))
            for name in ("first", "second")
        },
    )
    def root(first: pd.DataFrame, second: pd.DataFrame) -> pd.DataFrame:
        assert first is second
        return first

    plugin = PluginDefinition(metrics=[root, consume], location_operations=[make])
    executor = GraphExecutor(
        LocalRunner.from_definitions({"mixed": plugin}).catalog,
        {"mixed": BoundPlugin(plugin)},
        Mock(),
    )
    for _ in range(2):
        calls.clear()
        executor.graph.dependencies["root"]["second"].dependency.parameters["count"] = (
            "invalid" if broken else 2
        )
        if broken:
            with pytest.raises(GraphExecutionError) as error:
                executor.execute(
                    executor.plugins["mixed"].prepare_resolved_invocation("root", {})
                )
            assert error.value.node == "consume"
            assert error.value.path == ("root", "consume")
            assert error.value.receiving_input == "inputs.second"
        else:
            executor.execute(
                executor.plugins["mixed"].prepare_resolved_invocation("root", {})
            )
        assert calls == ([] if broken else ["make", "consume"])
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
        run_and_read(worker.run_metric_task, {})
    failure = native.meta["failure"]["error"]
    assert failure["node"] == "discover"
    assert failure["node_kind"] == "location_operation"
    assert failure["receiving_input"] == "inputs.data.locations.location"
    assert failure["path"] == "discovered"
    assert failure["dependency_path"] == ["root", "consume", "discover"]
    assert "private-token" not in str(failure)
    assert error.__cause__.__cause__ is secret


def test_empty_selection_never_acquires_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    for method in ("__init__", "setup", "resolve", "close"):
        failure = AssertionError(method)
        replacement = (
            create_autospec(Roads.resolve, side_effect=failure)
            if method == "resolve"
            else Mock(side_effect=failure)
        )
        monkeypatch.setattr(Roads, method, replacement)

    @native_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=Roads.id)},
        target=Roads.id,
        name="empty",
        description="Empty selection",
    )
    def empty(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        seen.append(source)
        return source

    @native_metric(
        code_revision="1",
        name="root",
        description="Empty root selection",
        outputs={"answer": VALUES},
        inputs={
            "source": LocationDependency(provider=Roads.id),
            "roads": LocationDependency("empty").bind(source=Input("source")),
        },
    )
    def root(source: gpd.GeoDataFrame, roads: gpd.GeoDataFrame) -> pd.DataFrame:
        assert source.empty
        assert roads.empty
        assert roads.crs is not None
        assert roads.crs.to_string() == "EPSG:27700"
        assert set(roads.columns) == {"location_id", "geometry", "original"}
        return pd.DataFrame({"value": [len(roads)]})

    definitions = {
        "metrics": NativePluginDefinition(metrics=[root]),
        "operations": NativePluginDefinition(location_operations=[empty]),
        "provider": NativePluginDefinition(resources={"roads": Roads}),
    }
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    native = native_job(monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    inputs = {"source": selection(provider=Roads.id).model_dump(mode="json")}
    asyncio.run(
        job_submission.submit_job(
            JobCreateRequest(metric="root", inputs=inputs),
            catalog=runtime.catalog,
            jobs=runtime.jobs,
            job_id_factory=lambda: native.id,
        )
    )
    assert seen == []
    (envelope,) = enqueue.call_args.args
    native.args = (envelope.model_dump(mode="json"),)
    local = LocalRunner.from_definitions(definitions).run("root", inputs=inputs)
    queued = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert (
        serialize_outputs("root", local)
        == queued["outputs"]
        == {"answer": [{"value": 0}]}
    )
    assert len(seen) == 2
    pd.testing.assert_frame_equal(seen[0], seen[1])
