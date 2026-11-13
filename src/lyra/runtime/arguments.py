"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

import json
from dataclasses import InitVar, dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.runtime.native_inputs import (
    NativeLocationInput,
    copy_frame,
    input_fingerprint,
)
from lyra.runtime.planning.graph import ResolvedDependency
from lyra.runtime.planning.parameters import forwarded_parameters
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from lyra.contracts.dependencies import LocationDependency
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.bindings import BoundInvocation
    from lyra.runtime.planning.graph import ResolvedLocationDependency


@dataclass(frozen=True)
class PreparedResolvedInvocation:
    """Effective scoped inputs backed by private native records."""

    node: str
    parameters: InitVar[BaseModel | None]
    _parameters: BaseModel | None = field(init=False, repr=False)
    locations: Mapping[str, NativeLocationInput]
    kind: Literal["metric", "location_operation"] = "metric"
    bound_use: BoundInvocation | None = field(default=None, kw_only=True)
    parameter_use_key: str = field(default="root", kw_only=True)
    original_locations: Mapping[str, NativeLocationInput] | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self, parameters: BaseModel | None) -> None:
        """Own mutable caller inputs and native records."""
        object.__setattr__(
            self,
            "_parameters",
            parameters.model_copy(deep=True) if parameters is not None else None,
        )
        object.__setattr__(
            self,
            "locations",
            MappingProxyType(
                {name: replace(value) for name, value in self.locations.items()}
            ),
        )
        if self.original_locations is not None:
            object.__setattr__(
                self,
                "original_locations",
                MappingProxyType(
                    {
                        name: replace(value)
                        for name, value in self.original_locations.items()
                    }
                ),
            )

    def select_primary(
        self, name: str, ids: frozenset[str]
    ) -> PreparedResolvedInvocation:
        """Narrow runtime demand while retaining the complete bound context.

        This internal boundary is used by execution, independently of request
        preparation. IDs belong to the named input's qualified provider scope.

        Returns:
            An invocation with selected primary records and complete context.

        Raises:
            ValueError: If demand includes an owner outside the bound selection.
        """
        original = (
            self.original_locations
            if self.original_locations is not None
            else self.locations
        )
        primary = original[name]
        if not ids.issubset(primary.ids):
            message = "Primary demand exceeds the original bound selection"
            raise ValueError(message)
        if ids == frozenset(primary.ids):
            return self.original()
        locations = dict(original)
        locations[name] = replace(
            primary, frame=primary.frame.loc[primary.frame[primary.id_column].isin(ids)]
        )
        for key, value in original.items():
            if key != name:
                selected = value.related_selection(locations[name])
                if selected is not None:
                    locations[key] = selected
        return replace(
            self,
            parameters=self.inspect_parameters(),
            locations=locations,
            original_locations=original,
        )

    def original(self) -> PreparedResolvedInvocation:
        """Restore original inputs for upstream binding and complete computation.

        Returns:
            The invocation with its complete bound selections.
        """
        if self.original_locations is None:
            return self
        return replace(
            self,
            parameters=self.inspect_parameters(),
            locations=self.original_locations
            if self.original_locations is not None
            else self.locations,
            original_locations=None,
        )

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
        if self.original_locations is not None:
            payload = json.loads(self.original().identity)
            payload["demand"] = {
                name: input_fingerprint(value, canonical=True)
                for name, value in self.locations.items()
            }
            return json.dumps(payload, sort_keys=True)
        if self.bound_use is not None:
            return json.dumps(
                {
                    "node": self.node,
                    "computation": self.bound_use.identity,
                    "content": {
                        name: input_fingerprint(value, canonical=True)
                        for name, value in self.locations.items()
                    },
                },
                sort_keys=True,
            )
        return _identity(self.kind, self.node, self._parameters, self.locations)

    def arguments(self) -> dict[str, object]:
        """Return ordinary handler inputs, with independently owned native frames."""
        result: dict[str, object] = {
            name: copy_frame(value.frame) for name, value in self.locations.items()
        }
        if self._parameters is not None:
            result["parameters"] = self._parameters.model_copy(deep=True)
        return result


def _identity(
    kind: str,
    node: str,
    parameters: BaseModel | None,
    locations: Mapping[str, NativeLocationInput],
) -> str:
    return json.dumps(
        {
            "kind": kind,
            "node": node,
            "parameters": parameter_values(parameters)
            if parameters is not None
            else None,
            "spatial": {
                name: input_fingerprint(value, canonical=True)
                for name, value in locations.items()
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
    location_input: Callable[
        [str, NativeLocationInput | LocationDependency], NativeLocationInput
    ]
    | None = None,
    effective_parameters: dict | None = None,
) -> PreparedResolvedInvocation:
    """Forward a resolved edge and validate using the producer implementation.

    The caller supplies the plugin owning the producer. Spatial values must
    already be resolved, or supplied by location_input for operation bindings.
    The callback receives the receiving name and supplied value or declaration;
    its closure retains the declaration owner for nested invocations and applies
    the accepted route before checking the receiving provider contract.

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
        arguments = _dependency_arguments(consumer, edge, location_input)
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
    location_input: Callable[
        [str, NativeLocationInput | LocationDependency], NativeLocationInput
    ]
    | None,
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
            value = binding
        else:
            source_name = binding.path if isinstance(binding, Input) else name
            if source_name not in locations:
                raise MetricInputError(producer.name, name, "missing spatial input")
            value = locations[source_name]
        if location_input is not None:
            arguments[name] = location_input(name, value)
        elif isinstance(value, NativeLocationInput):
            arguments[name] = value
        else:
            raise MetricInputError(
                producer.name, name, "location operation result evaluator is required"
            )
    return arguments
