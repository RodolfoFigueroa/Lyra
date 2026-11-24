"""Prepare graph arguments and job-local invocation identities without execution."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import MetricInputError
from lyra.contracts.value_identity import value_key
from lyra.runtime.native_inputs import (
    NativeLocationInput,
    copy_frame,
)
from lyra.runtime.planning.bindings import BoundLocation
from lyra.runtime.planning.graph import ResolvedDependency
from lyra.runtime.planning.parameters import forwarded_parameters
from lyra.sdk.parameters import parameter_values

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from lyra.contracts.dependencies import LocationDependency
    from lyra.contracts.locations import LocationContractReference
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
    # Scheduler-created native inputs may contain only misses; the bound use
    # retains the complete scope used to compute their identities.
    bound_identity: bool = field(default=False, kw_only=True)
    dependencies: Mapping[str, PreparedResolvedInvocation] = field(
        default_factory=dict, kw_only=True
    )
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
        selected_primary = primary.select_ids(ids)
        if selected_primary is primary:
            return self.original()
        locations = dict(original)
        locations[name] = selected_primary
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
        if self.bound_use is not None:
            keys = self.computation_keys
            if not self.bound_use.per_location:
                return keys[None]
            return value_key(
                {"partitions": keys}
                if keys
                else {"empty": self.computation_key(empty_context=True)}
            )
        return _identity(
            self.kind,
            self.node,
            self._parameters,
            self.original_locations
            if self.original_locations is not None
            else self.locations,
        )

    @property
    def computation_keys(self) -> dict[str | None, str]:
        """Logical values computed independently of batching.

        Raises:
            ValueError: If a partitioned computation has no primary input.
        """
        bound = self.bound_use
        if bound is None:
            return {
                None: _identity(self.kind, self.node, self._parameters, self.locations)
            }
        if self.bound_identity:
            groups = (
                frozenset(self.locations[bound.primary_input].ids)
                if bound.per_location and bound.primary_input is not None
                else None
            )
            keys = bound.demand(groups).computation_keys
            if keys is not None:
                return keys
        if not bound.per_location:
            return {None: self.original().computation_key()}
        primary = bound.primary_input
        if primary is None:
            message = "Partitioned computations require a primary input"
            raise ValueError(message)
        return {
            location_id: self.select_primary(
                primary, frozenset({location_id})
            ).computation_key()
            for location_id in sorted(set(self.locations[primary].ids))
        }

    def computation_key(self, *, empty_context: bool = False) -> str:
        """Return the key for the current applicable inputs.

        Raises:
            ValueError: If the invocation has no bound declaration.
        """
        bound = self.bound_use
        if bound is None:
            message = "Computation references require a bound invocation"
            raise ValueError(message)
        inputs = {}
        for name, value in self.locations.items():
            payload = value.identity_payload
            # Direct bindings have the same descriptor before acquisition.
            if not value.computations and name in bound.locations:
                upstream = bound.locations[name].upstream
                inputs[name] = (
                    value_key(
                        {
                            "provider": value.identity_payload["provider"],
                            "upstream": _empty_bound_identity(upstream),
                        }
                    )
                    if empty_context and not value.ids and upstream is not None
                    else bound.locations[name].identity
                    if upstream is not None
                    else BoundLocation(value.contract, value.ids).identity
                )
            else:
                inputs[name] = value_key(payload)
        for name, dependency in bound.dependencies.items():
            resolved = self.dependencies.get(name)
            if resolved is None:
                primary = bound.primary_input
                producer_primary = dependency.primary_input
                related = (
                    primary is not None
                    and producer_primary is not None
                    and bound.locations[primary]
                    is dependency.locations[producer_primary]
                )
                groups = (
                    frozenset(self.locations[primary].ids)
                    if related and primary in self.locations
                    else None
                )
                inputs[name] = dependency.demand(groups).reference(bound.outputs[name])
                continue
            primary = bound.primary_input
            producer_primary = dependency.primary_input
            selected = (
                resolved.locations[producer_primary].related_selection(
                    self.locations[primary]
                )
                if primary is not None and producer_primary is not None
                else None
            )
            inputs[name] = resolved.reference(
                bound.outputs[name],
                frozenset(selected.ids) if selected is not None else None,
            )
        return replace(
            bound.descriptor,
            inputs=inputs,
            computation_scope="per_location" if bound.per_location else "complete",
        ).key

    def reference(self, output: str, groups: frozenset[str] | None = None) -> dict:
        """Return compact named-output references with complete producer context.

        Raises:
            ValueError: If the invocation has no bound declaration.
        """
        bound = self.bound_use
        if bound is None:
            message = "Computation references require a bound invocation"
            raise ValueError(message)
        keys = self.computation_keys
        if not bound.per_location:
            result = {"computation": keys[None], "output": output}
            if groups is not None:
                result["groups"] = sorted(groups)
            return result
        partitions = {
            group: key
            for group, key in keys.items()
            if groups is None or group in groups
        }
        result = {
            "output": output,
            "partitions": partitions,
        }
        if not partitions:
            primary = bound.primary_input
            if primary is None:
                message = "Partitioned references require a primary input"
                raise ValueError(message)
            selected = self.select_primary(primary, frozenset())
            result["empty_context"] = selected.computation_key(empty_context=True)
        return result

    def location_contract(self, name: str) -> LocationContractReference:
        """Return the dataset binding that owns this computation input."""
        if self.bound_use is not None and self.bound_identity:
            return self.bound_use.locations[name].provider
        return self.locations[name].contract

    def arguments(self) -> dict[str, object]:
        """Return ordinary handler inputs, with independently owned native frames."""
        result: dict[str, object] = {
            name: copy_frame(value.frame) for name, value in self.locations.items()
        }
        if self._parameters is not None:
            result["parameters"] = self._parameters.model_copy(deep=True)
        return result


def _empty_bound_identity(bound: BoundInvocation) -> str:
    """Retain operation declarations without unrelated partition selections.

    Returns:
        A compact reference to the applicable upstream declaration context.
    """
    if not bound.per_location:
        return bound.identity
    inputs = {
        name: value_key(
            {
                "provider": location.provider.provider_scope.identity_payload,
                "ids": [],
                "upstream": _empty_bound_identity(location.upstream)
                if location.upstream
                else None,
            }
        )
        for name, location in bound.locations.items()
    }
    return replace(
        bound.descriptor, inputs=inputs, computation_scope="per_location"
    ).key


def _location_identity(value: NativeLocationInput) -> dict:
    return value.identity_payload


def _identity(
    kind: str,
    node: str,
    parameters: BaseModel | None,
    locations: Mapping[str, NativeLocationInput],
) -> str:
    return value_key(
        {
            "kind": kind,
            "node": node,
            "parameters": parameter_values(parameters)
            if parameters is not None
            else None,
            "spatial": {
                name: _location_identity(value) for name, value in locations.items()
            },
        },
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
        arguments = _dependency_arguments(
            consumer, edge, location_input, effective_parameters
        )
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
    effective_parameters: dict | None,
) -> dict[str, object]:
    producer = (
        edge.output.producer if isinstance(edge, ResolvedDependency) else edge.producer
    ).node
    if consumer.node != edge.consumer.node.name:
        raise MetricInputError(producer.name, "input", "consumer node mismatch")
    arguments: dict[str, object] = {}
    properties = producer.request_schema.get("properties", {})
    if isinstance(properties, dict) and "parameters" in properties:
        arguments["parameters"] = (
            effective_parameters
            if effective_parameters is not None
            else forwarded_parameters(consumer.parameter_values(), edge)
        )
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
