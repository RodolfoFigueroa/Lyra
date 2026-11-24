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
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition as NativePluginDefinition
from lyra.sdk.plugin import location_operation as native_operation
from lyra.sdk.plugin import metric as native_metric
from lyra.server import job_submission, worker
from test_support.location_operations import Places
from test_support.location_resolution import Provider, Roads, frame_features, selection
from test_support.runtime_inputs import frame_ids
from test_support.table_fixtures import value_frame
from tests.frame_assertions import assert_logical_equal
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
    worker.set_runner_temp_base(None)


@pytest.mark.parametrize("persistent", [False, True])
def test_nested_ownership_table_conversion_and_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, persistent: bool
) -> None:
    trace = []

    @native_metric(
        cache="full",
        code_revision="1",
        name="records",
        description="Records",
        outputs={"records": VALUES},
    )
    def records(parameters: Parameters) -> pd.DataFrame:
        trace.append(("records", parameters.count))
        return pd.DataFrame({"value": range(parameters.count)})

    @native_operation(
        cache="full",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
        name="construct",
        description="Select a parameterized prefix",
    )
    def construct(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        trace.append(("construct", parameters.count))
        return {key: (key,) for key in source.location_id.iloc[: parameters.count]}

    @native_operation(
        cache="full",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
        name="select",
        description="Select supplied places",
    )
    def select(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        trace.append(("select", parameters.count))
        assert len(source) in {3, 4}
        return {key: (key,) for key in source.location_id}

    @native_metric(
        cache="full",
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
        cache="full",
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
        assert_logical_equal(a, b)
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
        result_store_path=tmp_path / "worker-cache" if persistent else None,
    )
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
                    result_store_path=(
                        (tmp_path / "local-cache") / "results.sqlite3"
                        if persistent
                        else None
                    ),
                )
                result = serialize_outputs(
                    "root",
                    runner.run(
                        "root",
                        inputs={k: v for k, v in inputs.items() if k != "parameters"},
                        parameters=inputs.get("parameters"),
                    ).tables,
                )
            else:
                worker.initialize_worker("heavy", config=runtime.config)
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
            # Both automatic and explicit local paths retain values.
            assert trace == (
                []
                if iteration == 1
                else [
                    ("construct", count),
                    ("select", 9),
                    *([] if iteration == 2 else [("records", 2)]),
                    ("consume", 2),
                    ("consume", 2),
                ]
            )


def assert_mixed_outcome(run: Callable[[], dict], failure: str | None) -> None:
    if failure == "resolution":
        with pytest.raises(GraphExecutionError) as error:
            run()
        assert error.value.node == "discover"
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
        cache="full",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
        name="discover",
        description="Select supplied records",
    )
    def discover(source: gpd.GeoDataFrame) -> object:
        events.append("discover")
        if broken == "handler":
            message = "discovery failed"
            raise RuntimeError(message)
        if broken == "result":
            return ["a"]
        return {key: (key,) for key in source.location_id}

    @native_metric(
        cache="full",
        code_revision="1",
        inputs={"location": LocationDependency(provider=Provider.id)},
        name="consume",
        description="Consume",
        outputs={"consume": VALUES},
    )
    def consume(location: gpd.GeoDataFrame, context: RunContext) -> pd.DataFrame:
        contexts.append(context)
        assert location.location_id.tolist() == ["b", "a"]
        return pd.DataFrame({"value": [len(location)]})

    @native_metric(
        cache="full",
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
        lambda: local.run("root", inputs=inputs).tables,
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
    assert local.run("root", inputs=inputs).tables["answer"].value.tolist() == [2]
    assert not contexts[0].temp_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_operation_parameters_content_and_run_owned_results(*, empty: bool) -> None:
    calls = []

    class TargetOne(Places):
        id = "example:places1"

    class TargetTwo(Places):
        id = "example:places2"

    @native_operation(
        cache="full",
        code_revision="1",
        name="make",
        description="Select first scope",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=TargetOne.id,
    )
    def make(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        calls.append(parameters.count)
        return {} if empty else dict.fromkeys(source.location_id, ("0",))

    @native_operation(
        cache="full",
        code_revision="1",
        name="make_two",
        description="Select second scope",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=TargetTwo.id,
    )
    def make_two(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        calls.append(parameters.count)
        return {} if empty else dict.fromkeys(source.location_id, ("0",))

    def consumer(name: str, count: int) -> Callable[[gpd.GeoDataFrame], pd.DataFrame]:
        @native_metric(
            cache="full",
            code_revision="1",
            inputs={"location": LocationDependency(provider=f"example:places{count}")},
            name=name,
            description="Consume",
            outputs={name: TABLE},
        )
        def consume(location: gpd.GeoDataFrame) -> pd.DataFrame:
            calls.append(f"example:places{count}")
            assert len(frame_ids(location)) == (0 if empty else 1)
            return value_frame()

        return consume

    consume = consumer("consume", 1)
    consume_two = consumer("consume_two", 2)

    @native_metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={"source": LocationDependency(provider=Places.id)}
        | {
            name: Dependency(
                "consume" if count == 1 else "consume_two",
                producer="consume" if count == 1 else "consume_two",
            ).bind(
                location=LocationDependency(
                    "make" if count == 1 else "make_two",
                    parameters={"count": count},
                ).bind(source=Input("source"))
            )
            for name, count in [("a", 1), ("b", 1), ("c", 2)]
        },
    )
    def root(
        source: gpd.GeoDataFrame, a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame
    ) -> pd.DataFrame:
        assert source.location_id.tolist() == ["0"]
        assert a is not b
        assert_logical_equal(a, b)
        assert a is not c
        return a

    plugin = NativePluginDefinition(
        metrics=[root, consume, consume_two],
        location_operations=[make, make_two],
        resources={"source": Places, "one": TargetOne, "two": TargetTwo},
    )
    executor = GraphExecutor(
        LocalRunner.from_definitions({"mixed": plugin}).catalog,
        lambda node: Mock(spec=RunContext, node=node),
    )
    for _ in range(2):
        calls.clear()
        executor.execute(
            executor.catalog.implementations["mixed"].prepare_resolved_invocation(
                "root", {"source": sample_source()}
            )
        )
        assert sorted(calls, key=str) == sorted(
            [1, "example:places1", 2, "example:places2"], key=str
        )
    with pytest.raises(GraphExecutionError, match="roots must be metrics"):
        executor.execute(
            executor.catalog.implementations["mixed"].prepare_resolved_invocation(
                "make", {"source": sample_source(), "parameters": {}}
            )
        )


def test_preparation_failure_and_next_run_start_fresh() -> None:
    calls = []
    broken = True

    @native_operation(
        cache="full",
        code_revision="1",
        name="make",
        description="Map supplied records",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def make(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        assert parameters.count == 2
        calls.append("make")
        return {key: (key,) for key in source.location_id}

    @native_metric(
        cache="full",
        code_revision="1",
        inputs={"location": LocationDependency(provider=Places.id)},
        name="consume",
        description="Consume",
        outputs={"consume": TABLE},
    )
    def consume(location: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
        assert frame_ids(location) == ["0"]
        calls.append("consume")
        assert parameters.count == 2
        return value_frame()

    @native_metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={"source": LocationDependency(provider=Places.id)}
        | {
            name: Dependency(
                "consume",
                parameters={"count": 2},
                producer="consume",
            ).bind(location=LocationDependency("make").bind(source=Input("source")))
            for name in ("first", "second")
        },
    )
    def root(
        source: gpd.GeoDataFrame, first: pd.DataFrame, second: pd.DataFrame
    ) -> pd.DataFrame:
        assert source.location_id.tolist() == ["0"]
        assert first is not second
        pd.testing.assert_frame_equal(first, second)
        return first

    plugin = NativePluginDefinition(
        metrics=[root, consume],
        location_operations=[make],
        resources={"source": Places},
    )
    executor = GraphExecutor(
        LocalRunner.from_definitions({"mixed": plugin}).catalog,
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
                    executor.catalog.implementations[
                        "mixed"
                    ].prepare_resolved_invocation("root", {"source": sample_source()})
                )
            assert error.value.node == "consume"
            assert error.value.path == ("root", "consume")
            assert error.value.receiving_input == "inputs.second"
        else:
            executor.execute(
                executor.catalog.implementations["mixed"].prepare_resolved_invocation(
                    "root", {"source": sample_source()}
                )
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
        cache="full",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Roads.id)},
        target_provider=Roads.id,
        name="empty",
        description="Empty selection",
    )
    def empty(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        seen.append(source)
        return {key: (key,) for key in source.location_id}

    @native_metric(
        cache="full",
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
        assert set(roads.columns) == {
            "source_location_id",
            "location_id",
            "geometry",
            "original",
        }
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
    local = LocalRunner.from_definitions(definitions).run("root", inputs=inputs).tables
    queued = run_and_read(worker.run_metric_task, envelope.model_dump(mode="json"))
    assert (
        serialize_outputs("root", local)
        == queued["outputs"]
        == {"answer": [{"value": 0}]}
    )
    assert seen == []


def sample_source() -> NativeLocationInput:
    schema = capture_provider("source", Places)
    frame = frame_features(["0"]).drop(columns="original")
    frame["count"] = 4
    return NativeLocationInput(frame, schema.contract.reference)
