"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, overload

from lyra.sdk.dependencies import Input
from lyra.sdk.errors import MetricInputError
from lyra.sdk.graph import ResolvedDependency, parameter_contract
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.sdk.dependencies import LocationDependencyManifest
    from lyra.sdk.graph import PluginGraph, ResolvedLocationDependency
    from lyra.sdk.models.location import Locations, LocationSelection
    from lyra.sdk.plugin import PluginDefinition


def _parameter_values(model: BaseModel) -> dict[str, object]:
    """Read effective fields independently of serialization exclusion options.

    Returns:
        All declared fields with nested models converted to JSON containers.
    """
    return {
        name: _parameter_value(getattr(model, name))
        for name in type(model).model_fields
    }


def _parameter_value(value: object) -> object:
    """Normalize the SDK's supported nested models and JSON containers.

    Returns:
        The scalar value or recursively normalized container.
    """
    if isinstance(value, BaseModel):
        return _parameter_values(value)
    if isinstance(value, dict):
        return {key: _parameter_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_parameter_value(item) for item in value]
    return value


@dataclass(frozen=True)
class PreparedSelectionInvocation:
    """Validated public selections and effective parameters awaiting execution.

    Identity is available only after selections resolve in the execution scope.
    """

    node: str
    parameters: BaseModel | None
    selections: dict[str, LocationSelection]


@dataclass(frozen=True)
class PreparedInvocation:
    """Validated ordinary inputs, excluding context and dependency tables.

    Treat the contained models as read-only throughout graph execution.
    """

    node: str
    parameters: BaseModel | None
    spatial: dict[str, Locations]
    kind: Literal["metric", "location_operation"] = "metric"

    @property
    def identity(self) -> str:
        """Canonical serialized inputs for reuse within one job.

        Returns:
            A stable key preserving list order and literal geometry encoding.
        """
        return _identity(self.kind, self.node, self.parameters, self.spatial)

    def arguments(self) -> dict[str, object]:
        """Return handler inputs before context and dependency injection."""
        result: dict[str, object] = dict(self.spatial)
        if self.parameters is not None:
            result["parameters"] = self.parameters
        return result


@dataclass(frozen=True)
class PreparedResolvedInvocation:
    """Effective scoped inputs. Shared location models are read-only by contract."""

    node: str
    parameters: BaseModel | None
    locations: dict[str, Locations]
    kind: Literal["metric", "location_operation"] = "metric"

    @property
    def identity(self) -> str:
        """The scoped identity used for job reuse and persistent caching."""
        return _identity(self.kind, self.node, self.parameters, self.locations)

    def arguments(self) -> dict[str, object]:
        """Return ordinary handler inputs, including collection scope."""
        result: dict[str, object] = dict(self.locations)
        if self.parameters is not None:
            result["parameters"] = self.parameters
        return result


def _identity(
    kind: str,
    node: str,
    parameters: BaseModel | None,
    locations: Mapping[str, BaseModel],
) -> str:
    return json.dumps(
        {
            "kind": kind,
            "node": node,
            "parameters": _parameter_values(parameters)
            if parameters is not None
            else None,
            "spatial": {
                name: value.model_dump(mode="json") for name, value in locations.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@overload
def prepare_dependency(
    consumer: PreparedInvocation,
    edge: ResolvedDependency | ResolvedLocationDependency,
    plugin: PluginDefinition,
    *,
    location_result: Callable[[str, LocationDependencyManifest], Locations]
    | None = None,
) -> PreparedInvocation: ...


@overload
def prepare_dependency(
    consumer: PreparedResolvedInvocation,
    edge: ResolvedDependency | ResolvedLocationDependency,
    plugin: PluginDefinition,
    *,
    location_result: Callable[[str, LocationDependencyManifest], Locations]
    | None = None,
) -> PreparedResolvedInvocation: ...


def prepare_dependency(
    consumer: PreparedInvocation | PreparedResolvedInvocation,
    edge: ResolvedDependency | ResolvedLocationDependency,
    plugin: PluginDefinition,
    *,
    location_result: Callable[[str, LocationDependencyManifest], Locations]
    | None = None,
) -> PreparedInvocation | PreparedResolvedInvocation:
    """Forward a resolved edge and validate using the producer implementation.

    The caller supplies the plugin owning the producer. Spatial values must
    already be resolved, or supplied by location_result for operation bindings.
    The callback receives the receiving name and declaration; its closure must
    retain this declaration owner when preparing nested invocations.

    Returns:
        The producer's effective inputs, ready for dependency resolution or reuse.

    Raises:
        MetricInputError: If forwarding or producer validation fails. The cause
            retains the original validation error and its field path.
    """
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    )
    try:
        arguments = _dependency_arguments(consumer, edge, location_result)
        if isinstance(consumer, PreparedResolvedInvocation):
            return plugin.prepare_resolved_invocation(producer.node.name, arguments)
        return plugin.prepare_invocation(producer.node.name, arguments)
    except MetricInputError as exc:
        raise MetricInputError(
            producer.node.name,
            exc.path,
            f"{edge.diagnostic}: {exc.message}",
        ) from exc


def _dependency_arguments(
    consumer: PreparedInvocation | PreparedResolvedInvocation,
    edge: ResolvedDependency | ResolvedLocationDependency,
    location_result: Callable[[str, LocationDependencyManifest], Locations] | None,
) -> dict[str, object]:
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    if consumer.node != edge.consumer.node.name:
        raise MetricInputError(producer.name, "input", "consumer node mismatch")
    source = (
        _parameter_values(consumer.parameters)
        if consumer.parameters is not None
        else {}
    )
    values = {
        name: source[name]
        for name in parameter_contract(producer).get("properties", {})
        if name in source
    }
    for name, binding in edge.dependency.parameters.items():
        if isinstance(binding, Input):
            field = binding.path.split(".")[1]
            if field not in source:
                raise MetricInputError(
                    producer.name, binding.path, "missing referenced input"
                )
            values[name] = source[field]
        else:
            values[name] = binding.value
    arguments: dict[str, object] = {}
    properties = producer.request_schema.get("properties", {})
    if isinstance(properties, dict) and "parameters" in properties:
        arguments["parameters"] = values
    locations = (
        consumer.locations
        if isinstance(consumer, PreparedResolvedInvocation)
        else consumer.spatial
    )
    bindings = edge.dependency.locations
    for name in producer.spatial_inputs:
        binding = bindings.get(name)
        if binding is not None and not isinstance(binding, Input):
            if location_result is None:
                raise MetricInputError(
                    producer.name,
                    name,
                    "location operation result evaluator is required",
                )
            arguments[name] = location_result(name, binding)
            continue
        source_name = binding.path if isinstance(binding, Input) else name
        if source_name not in locations:
            raise MetricInputError(producer.name, name, "missing spatial input")
        arguments[name] = locations[source_name]
    return arguments


@dataclass(frozen=True)
class InvocationPreparer:
    """Prepare mixed bindings using an injected operation evaluator.

    The evaluator receives validated effective operation inputs. It may supply a
    result from memory or execute it in the caller's resource scope. This class
    validates and normalizes that result before preparing its consumer; it does
    not invoke handlers, resolve table dependencies, or manage reuse.
    """

    graph: PluginGraph
    plugins: Mapping[str, PluginDefinition]
    evaluate_location: Callable[[PreparedResolvedInvocation], object]

    def prepare(
        self,
        owner: PreparedResolvedInvocation,
        edge: ResolvedDependency | ResolvedLocationDependency,
    ) -> PreparedResolvedInvocation:
        """Return effective target inputs, keeping nested references on owner."""
        producer = (
            edge.output.producer
            if isinstance(edge, ResolvedDependency)
            else edge.producer
        )
        path = (
            f"inputs.{edge.input_name}"
            if isinstance(edge, ResolvedDependency)
            else edge.binding_path
        )

        def result(name: str, binding: LocationDependencyManifest) -> Locations:
            nested = self.graph.location_dependencies[owner.node][
                f"{path}.locations.{name}"
            ]
            prepared = self.prepare(owner, nested)
            plugin = self.plugins[nested.producer.distribution]
            try:
                return plugin.normalize_location_result(
                    binding.operation, self.evaluate_location(prepared)
                )
            except (TypeError, ValueError) as exc:
                raise MetricInputError(
                    producer.node.name, name, f"{nested.diagnostic}: {exc}"
                ) from exc

        return prepare_dependency(
            owner,
            edge,
            self.plugins[producer.distribution],
            location_result=result,
        )
