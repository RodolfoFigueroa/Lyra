"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

import json
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.data.locations import location_fingerprint
from lyra.runtime.planning.graph import ResolvedDependency
from lyra.runtime.planning.parameters import forwarded_parameters
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from lyra.contracts.dependencies import LocationDependency
    from lyra.data.locations import Locations
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.graph import ResolvedLocationDependency


@dataclass(frozen=True)
class PreparedResolvedInvocation:
    """Effective scoped inputs backed by immutable location snapshots."""

    node: str
    parameters: InitVar[BaseModel | None]
    _parameters: BaseModel | None = field(init=False, repr=False)
    locations: Mapping[str, Locations]
    kind: Literal["metric", "location_operation"] = "metric"

    def __post_init__(self, parameters: BaseModel | None) -> None:
        """Own mutable caller inputs while sharing immutable locations."""
        object.__setattr__(
            self,
            "_parameters",
            parameters.model_copy(deep=True) if parameters is not None else None,
        )
        object.__setattr__(self, "locations", MappingProxyType(dict(self.locations)))

    def inspect_parameters(self) -> BaseModel | None:
        """Return detached effective parameters for inspection."""
        return (
            self._parameters.model_copy(deep=True)
            if self._parameters is not None
            else None
        )

    def parameter_values(self) -> dict:
        """Return the effective JSON values for forwarding and preparation."""
        return (
            parameter_values(self._parameters) if self._parameters is not None else {}
        )

    @property
    def identity(self) -> str:
        """The scoped identity used for job reuse and persistent caching."""
        return _identity(self.kind, self.node, self._parameters, self.locations)

    def arguments(self) -> dict[str, object]:
        """Return ordinary handler inputs, including collection scope."""
        result: dict[str, object] = dict(self.locations)
        if self._parameters is not None:
            result["parameters"] = self._parameters.model_copy(deep=True)
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
            "parameters": parameter_values(parameters)
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
    location_result: Callable[[str, LocationDependency], Locations] | None = None,
    effective_parameters: dict | None = None,
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
        if effective_parameters is not None and "parameters" in arguments:
            arguments["parameters"] = effective_parameters
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
    location_result: Callable[[str, LocationDependency], Locations] | None,
) -> dict[str, object]:
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    if consumer.node != edge.consumer.node.name:
        raise MetricInputError(producer.name, "input", "consumer node mismatch")
    source = consumer.parameter_values()
    values = forwarded_parameters(source, edge)
    arguments: dict[str, object] = {}
    properties = producer.request_schema.get("properties", {})
    if isinstance(properties, dict) and "parameters" in properties:
        arguments["parameters"] = values
    locations = consumer.locations
    bindings = edge.dependency.locations
    for name, declaration in producer.location_inputs.items():
        if (
            declaration.dependency is not None
            and declaration.dependency.operation is not None
        ):
            continue
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
