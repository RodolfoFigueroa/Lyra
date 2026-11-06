"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.data.locations import location_fingerprint
from lyra.runtime.planning.graph import ResolvedDependency, parameter_contract

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.dependencies import LocationDependencyManifest
    from lyra.data.locations import Locations
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.graph import ResolvedLocationDependency


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
class PreparedResolvedInvocation:
    """Effective scoped inputs backed by immutable location snapshots."""

    node: str
    parameters: BaseModel | None
    locations: dict[str, Locations]
    kind: Literal["metric", "location_operation"] = "metric"

    def __post_init__(self) -> None:
        """Own mutable caller inputs while sharing immutable locations."""
        if self.parameters is not None:
            object.__setattr__(
                self, "parameters", self.parameters.model_copy(deep=True)
            )
        object.__setattr__(self, "locations", dict(self.locations))

    @property
    def identity(self) -> str:
        """The scoped identity used for job reuse and persistent caching."""
        return _identity(self.kind, self.node, self.parameters, self.locations)

    def arguments(self) -> dict[str, object]:
        """Return ordinary handler inputs, including collection scope."""
        result: dict[str, object] = dict(self.locations)
        if self.parameters is not None:
            result["parameters"] = self.parameters.model_copy(deep=True)
        return result


def _identity(
    kind: str,
    node: str,
    parameters: BaseModel | None,
    locations: Mapping[str, Locations],
) -> str:
    return json.dumps(
        {
            "kind": kind,
            "node": node,
            "parameters": _parameter_values(parameters)
            if parameters is not None
            else None,
            "spatial": {
                name: location_fingerprint(value) for name, value in locations.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def prepare_dependency(
    consumer: PreparedResolvedInvocation,
    edge: ResolvedDependency | ResolvedLocationDependency,
    plugin: BoundPlugin,
    *,
    location_result: Callable[[str, LocationDependencyManifest], Locations]
    | None = None,
) -> PreparedResolvedInvocation:
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
        return plugin.prepare_resolved_invocation(producer.node.name, arguments)
    except MetricInputError as exc:
        raise MetricInputError(
            producer.node.name,
            exc.path,
            f"{edge.diagnostic}: {exc.message}",
        ) from exc


def _dependency_arguments(
    consumer: PreparedResolvedInvocation,
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
    locations = consumer.locations
    bindings = edge.dependency.locations
    for name in producer.location_inputs:
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
