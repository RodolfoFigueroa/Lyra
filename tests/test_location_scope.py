"""Operation completion, target acquisition, and ownership in one run scope."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from unittest.mock import Mock

import geopandas as gpd
import pandera.pandas as pa
import pytest
from pydantic import Field
from typing_extensions import override

from lyra.contracts.errors import LocationProviderContractError
from lyra.contracts.locations import LocationSelection
from lyra.contracts.plugins import NODE_MANIFEST_ADAPTER
from lyra.runtime.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.runtime.execution import GraphExecutionError, RunState
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.operations import OperationResult, execute_operation
from lyra.runtime.resources import ResourceScope
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.parameters import MetricParameters
from test_support.location_operations import Places
from test_support.operation_execution import OperationExecutor


class Parameters(MetricParameters):
    targets: list[str] = Field(
        default_factory=lambda: ["x", "y"], description="Target IDs"
    )


@dataclass
class Execution:
    plugin: BoundPlugin
    resolver: LocationResolver
    invocation: PreparedResolvedInvocation
    events: list[tuple[str, ...]]
    executor: OperationExecutor
    state: RunState

    def run(self) -> OperationResult:
        return self.executor.dispatch(self.invocation, self.state, Mock())


@contextmanager
def execution(
    handler: Callable,
    *,
    ids: tuple[str, ...] = ("a", "b", "c"),
    same_provider: bool = False,
    missing: bool = False,
    parameters: dict | None = None,
) -> Generator[Execution]:
    events: list[tuple[str, ...]] = []

    class Source(Places):
        id = "source"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append((self.id, *ids))
            return super().resolve(ids)

        @override
        def close(self) -> None:
            events.append((self.id, "close"))

    class Target(Source):
        id = "target"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            frame = super().resolve(ids)
            return frame.iloc[:0] if missing else frame

    operation = location_operation(
        name="select",
        description="Select target IDs",
        code_revision="1",
        inputs={"declared_source": LocationDependency(provider=Source.id)},
        target_provider=Source.id if same_provider else Target.id,
    )(handler)
    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                location_operations=[operation],
                resources={"source": Source, "target": Target},
            )
        }
    )
    plugin = runner.catalog.implementations["test"]
    manifest = plugin.node_manifest("select")
    assert manifest is not None
    assert NODE_MANIFEST_ADAPTER.validate_json(manifest.model_dump_json()) == manifest
    scope = ResourceScope(plugin.resources)
    with scope.activate():
        resolver = LocationResolver(scope, providers=runner.catalog.provider_registry)
        inputs: dict[str, object] = dict(
            resolver.resolve(
                {
                    "declared_source": LocationSelection(
                        provider=Source.id, ids=list(ids)
                    )
                }
            )
        )
        if parameters is not None:
            inputs["parameters"] = parameters
        invocation = plugin.prepare_resolved_invocation("select", inputs)
        state = RunState()
        state.resolver = resolver
        executor = OperationExecutor(
            runner.catalog, runner.catalog.implementations, Mock()
        )
        yield Execution(plugin, resolver, invocation, events, executor, state)


def test_positional_mapping_completion_isolation_and_shared_acquisition() -> None:
    mapping = {"a": ("x", "y"), "b": ("y",)}

    def handler(renamed: gpd.GeoDataFrame, /) -> dict[str, tuple[str, ...]]:
        renamed.loc[:, "location_id"] = "changed"
        return mapping

    with execution(handler) as run:
        result = run.run()
        mapping["a"] = ()
        assert result.groups == {"a": ("x", "y"), "b": ("y",), "c": ()}
        assert result.targets.ids == ("x", "y")
        assert result.source_scope.collection == "source"
        assert result.targets.contract.collection == "target"
        assert run.invocation.locations["declared_source"].ids == ("a", "b", "c")
        assert run.events == [("source", "a", "b", "c"), ("target", "x", "y")]
        repeated = run.run()
        assert repeated is result
        assert repeated.targets.ids == ("x", "y")
        assert run.events == [("source", "a", "b", "c"), ("target", "x", "y")]
        assert ("target", "close") not in run.events
    assert run.events[-2:] == [("target", "close"), ("source", "close")]


@pytest.mark.parametrize("explicit", [False, True])
def test_empty_groups_complete_without_target_initialization(*, explicit: bool) -> None:
    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return dict.fromkeys(frame.location_id, ()) if explicit else {}

    with execution(handler) as run:
        result = run.run()
        assert result.groups == dict.fromkeys(("a", "b", "c"), ())
        assert result.targets.ids == ()
        assert result.targets.contract.collection == "target"
        assert result.targets.frame.crs == "EPSG:4326"
        assert run.events == [("source", "a", "b", "c")]


def test_empty_source_skips_callback_and_acquisition() -> None:
    def handler(frame: gpd.GeoDataFrame) -> object:
        pytest.fail(str(frame))

    with execution(handler, ids=()) as run:
        result = run.run()
        assert result.groups == {}
        assert result.targets.contract.collection == "target"
        assert run.events == []


@pytest.mark.parametrize(
    "invalid",
    [None, [], {"a": ["x"]}, {"a": (1,)}, {"a": ("x", "x")}, {"changed": ("x",)}],
)
def test_invalid_mappings_never_complete_or_acquire_targets(invalid: object) -> None:
    def handler(frame: gpd.GeoDataFrame) -> object:
        frame.loc[:, "location_id"] = "changed"
        return invalid

    with execution(handler) as run:
        with pytest.raises(GraphExecutionError, match=r"validation error|Operation"):
            run.run()
        assert run.events == [("source", "a", "b", "c")]
        assert run.invocation.identity not in run.state
        assert [event.outcome for event in run.state.observations] == ["started"]


def test_missing_targets_fail_and_cleanup_without_completion() -> None:
    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {frame.location_id.iloc[0]: ("missing",)}

    with execution(handler, missing=True) as run:
        with pytest.raises(
            GraphExecutionError, match="match requested IDs exactly"
        ) as error:
            run.run()
        assert isinstance(error.value.__cause__, LocationProviderContractError)
        assert run.invocation.identity not in run.state
        assert [event.outcome for event in run.state.observations] == ["started"]
    assert run.events[-2:] == [("target", "close"), ("source", "close")]


def test_same_provider_reuses_records_and_acquires_only_missing_targets() -> None:
    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {frame.location_id.iloc[0]: ("b", "x")}

    with execution(handler, same_provider=True) as run:
        result = run.run()
        assert result.targets.ids == ("b", "x")
        assert result.targets.contract == result.source_scope
        assert run.events == [("source", "a", "b", "c"), ("source", "x")]


def test_mutable_parameters_are_independent_and_defaults_apply() -> None:
    def handler(
        renamed: gpd.GeoDataFrame, /, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        targets = tuple(parameters.targets)
        parameters.targets.clear()
        return {renamed.location_id.iloc[0]: targets}

    with execution(handler, parameters={}) as run:
        result = run.run()
        assert result.groups["a"] == ("x", "y")
        assert run.invocation.parameter_values() == {"targets": ["x", "y"]}


class CallerParameters(MetricParameters):
    requested: list[str] = Field(description="Requested target IDs")


def test_declared_binding_and_forwarded_fields_between_distinct_models() -> None:
    @location_operation(
        name="choose",
        description="Choose targets",
        code_revision="1",
        inputs={"declared": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def choose(
        arbitrary: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        result = dict.fromkeys(arbitrary.location_id, tuple(parameters.targets))
        parameters.targets.clear()
        return result

    @metric(
        name="caller",
        description="Caller",
        code_revision="1",
        inputs={
            "seed": LocationDependency(provider=Places.id),
            "selected": LocationDependency(
                "choose", parameters={"targets": Input("parameters.requested")}
            ).bind(declared=Input("seed")),
        },
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def caller(
        seed: gpd.GeoDataFrame,
        selected: gpd.GeoDataFrame,
        *,
        parameters: CallerParameters,
    ) -> object:
        pytest.fail(f"Unexpected consumer: {seed}, {selected}, {parameters}")

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[caller],
                location_operations=[choose],
                resources={"places": Places},
            )
        }
    )
    plugin = runner.catalog.implementations["test"]
    scope = ResourceScope(plugin.resources)
    with scope.activate():
        resolver = LocationResolver(scope, providers=runner.catalog.provider_registry)
        source = resolver.resolve(
            {"seed": LocationSelection(provider=Places.id, ids=["a"])}
        )
        owner = plugin.prepare_resolved_invocation(
            "caller", source | {"parameters": {"requested": ["x"]}}
        )
        edge = runner.catalog.graph.location_dependencies["caller"]["inputs.selected"]
        prepared = prepare_dependency(owner, edge, plugin)
        result = execute_operation(plugin, prepared, resolver)
        assert result.groups == {"a": ("x",)}
        assert prepared.parameter_values() == {"targets": ["x"]}
        assert owner.parameter_values() == {"requested": ["x"]}


def test_different_uses_reuse_targets_and_acquire_only_the_difference() -> None:
    def handler(
        frame: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        return {frame.location_id.iloc[0]: tuple(parameters.targets)}

    with execution(handler, parameters={"targets": ["x", "y"]}) as run:
        first = run.run()
        run.invocation = replace(
            run.invocation, parameters=Parameters(targets=["y", "z"])
        )
        second = run.run()
        assert first.targets.ids == ("x", "y")
        assert second.targets.ids == ("y", "z")
        assert run.events == [
            ("source", "a", "b", "c"),
            ("target", "x", "y"),
            ("target", "z"),
        ]
        assert len(run.state) == 2


def test_repeated_source_rows_keep_working_rows_and_distinct_groups() -> None:
    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        assert frame.location_id.tolist() == ["a", "a", "b"]
        return {"a": ("x",), "b": ()}

    with execution(handler, ids=("a", "b")) as run:
        source = run.invocation.locations["declared_source"]
        repeated = NativeLocationInput(source.frame.iloc[[0, 0, 1]], source.contract)
        run.invocation = replace(
            run.invocation, parameters=None, locations={"declared_source": repeated}
        )
        result = run.run()
        assert result.groups == {"a": ("x",), "b": ()}
        assert result.targets.ids == ("x",)


def test_handler_failure_is_not_memoized_and_retry_can_complete() -> None:
    attempts = []

    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        attempts.append(len(frame))
        if len(attempts) == 1:
            message = "handler failed"
            raise RuntimeError(message)
        return {"a": ("x",)}

    with execution(handler) as run:
        with pytest.raises(GraphExecutionError, match="handler failed"):
            run.run()
        assert not run.state
        assert run.events == [("source", "a", "b", "c")]
        assert run.run().groups == {"a": ("x",), "b": (), "c": ()}
        assert attempts == [3, 3]
        assert [event.outcome for event in run.state.observations] == [
            "started",
            "started",
            "completed",
        ]


def test_equal_ids_in_different_providers_require_target_acquisition() -> None:
    def handler(frame: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in frame.location_id}

    with execution(handler, ids=("a",)) as run:
        result = run.run()
        assert result.source_scope != result.targets.contract
        assert result.groups == {"a": ("a",)}
        assert run.events == [("source", "a"), ("target", "a")]
