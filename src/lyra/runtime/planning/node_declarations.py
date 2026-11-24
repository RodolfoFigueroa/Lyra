"""Native node declarations staged for managed request and runtime integration.

Compile live declarations against the same combined provider registry used for
acquisition. This module does not route requests or invoke handlers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, TypeAdapter

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.metric_scope import (
    CachePolicy,
    OutputScope,
    primary_input,
    validate_cache,
)
from lyra.contracts.origins import Origin
from lyra.contracts.plugins import validate_metric_name
from lyra.sdk.handler import _handler_arguments, _handler_hints, _operation_arguments

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.sdk.resources import resource_annotation


@dataclass(frozen=True)
class NodeDeclaration:
    """Executable native declaration, independent of inspection dictionaries."""

    name: str
    function: Callable[..., object]
    inputs: Mapping[str, Dependency | LocationDependency] = field(default_factory=dict)
    outputs: tuple[str, ...] = ()
    kind: Literal["metric", "location_operation"] = "metric"
    target: str | None = None
    origins: Sequence[Origin] = ()
    cache: CachePolicy = "off"
    target_selector: Callable[..., object] | None = None

    @property
    def primary_input(self) -> str | None:
        """The receiving input designated as owner."""
        if self.kind == "location_operation":
            return next(iter(self.inputs), None)
        return primary_input(self.inputs)

    @property
    def scope(self) -> OutputScope:
        """Ownership shared by all metric outputs."""
        return "location" if self.primary_input is not None else "unpartitioned"


@dataclass(frozen=True)
class CompiledNode:
    """Captured handler, named bindings, complete provider records and own origins."""

    declaration: NodeDeclaration
    locations: Mapping[str, RegisteredProvider]
    origins: frozenset[Origin]
    parameters: type[BaseModel] | None


def compile_node_declarations(
    providers: Mapping[str, RegisteredProvider],
    declarations: Sequence[NodeDeclaration],
) -> Mapping[str, CompiledNode]:
    """Resolve native nodes across the combined active provider catalog.

    Returns:
        Captured executable declarations with each location's required provider.

    Raises:
        ValueError: If names, targets, bindings, capabilities or cycles are invalid.
    """
    nodes: dict[str, CompiledNode] = {}
    for original in declarations:
        _validate_declaration(original)
        if original.name in nodes:
            msg = f"Duplicate node {original.name!r}"
            raise ValueError(msg)
        declaration = NodeDeclaration(
            name=original.name,
            function=original.function,
            inputs=MappingProxyType(deepcopy(dict(original.inputs))),
            outputs=tuple(original.outputs),
            kind=original.kind,
            target=original.target,
            origins=tuple(original.origins),
            cache=original.cache,
            target_selector=original.target_selector,
        )
        origins = TypeAdapter(frozenset[Origin]).validate_python(declaration.origins)
        parameters = _declaration_parameters(declaration)
        _validate_operation(declaration, origins)
        nodes[declaration.name] = CompiledNode(declaration, {}, origins, parameters)
    predecessors: dict[str, set[str]] = {}
    for name, node in nodes.items():
        required: dict[str, RegisteredProvider] = {}
        predecessors[name] = set()
        if node.declaration.target is not None:
            target = _provider(providers, node.declaration.target)
            if (
                node.declaration.target_selector is not None
                and not target.query_capable
            ):
                message = (
                    f"Target {node.declaration.target!r} "
                    "requires managed query capability"
                )
                raise ValueError(message)
        for key, dependency in node.declaration.inputs.items():
            _resolve_binding(node, dependency, nodes, providers, predecessors[name])
            if isinstance(dependency, LocationDependency):
                provider_id = dependency.provider
                if provider_id is None:
                    operation = nodes[str(dependency.operation)].declaration
                    provider_id = operation.target
                required[key] = _provider(providers, provider_id)
        _validate_input_cycles(node, nodes)
        nodes[name] = CompiledNode(
            node.declaration, MappingProxyType(required), node.origins, node.parameters
        )
    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        msg = f"Node dependency cycle: {exc.args[1]}"
        raise ValueError(msg) from exc
    return MappingProxyType(nodes)


def _declaration_parameters(declaration: NodeDeclaration) -> type[BaseModel] | None:
    if declaration.kind == "location_operation":
        parameters = _operation_arguments(
            declaration.name, declaration.function, declaration.target_selector
        )
    else:
        parameters, spatial, _ = _handler_arguments(
            declaration.name, declaration.function, declaration.inputs
        )
        if spatial != {
            key
            for key, value in declaration.inputs.items()
            if isinstance(value, LocationDependency)
        }:
            msg = "Every location source must be declared in inputs"
            raise ValueError(msg)
    return parameters


def _validate_input_cycles(
    owner: CompiledNode, nodes: Mapping[str, CompiledNode]
) -> None:
    predecessors = {
        key: _location_supplies(value, nodes)
        for key, value in owner.declaration.inputs.items()
        if isinstance(value, LocationDependency)
    }
    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        message = f"Location input cycle in {owner.declaration.name!r}: {exc.args[1]}"
        raise ValueError(message) from exc


def _location_supplies(
    dependency: LocationDependency, nodes: Mapping[str, CompiledNode]
) -> set[str]:
    if dependency.provider is not None:
        return set()
    producer = nodes[str(dependency.operation)].declaration
    supplied: set[str] = set()
    for key, value in producer.inputs.items():
        if not isinstance(value, LocationDependency) or value.provider is None:
            continue
        binding = dependency.locations.get(key, Input(key))
        if isinstance(binding, Input):
            supplied.add(binding.path)
        else:
            supplied.update(_location_supplies(binding, nodes))
    return supplied


def _provider(
    providers: Mapping[str, RegisteredProvider], provider_id: str | None
) -> RegisteredProvider:
    if provider_id is not None:
        TypeAdapter(str).validate_python(provider_id, strict=True)
    if provider_id is None or provider_id not in providers:
        msg = f"Unknown provider or operation target {provider_id!r}"
        raise ValueError(msg)
    return providers[provider_id]


def _validate_operation(node: NodeDeclaration, origins: frozenset[Origin]) -> None:
    if node.kind != "location_operation":
        if node.target is not None or node.target_selector is not None:
            msg = "Only operations declare a target"
            raise ValueError(msg)
        return
    sources = list(node.inputs.values())
    if (
        len(sources) != 1
        or not isinstance(sources[0], LocationDependency)
        or sources[0].provider is None
    ):
        msg = "Operations require exactly one provider location source"
        raise ValueError(msg)
    if node.target is None:
        msg = "Operations require a provider target"
        raise ValueError(msg)
    if origins:
        msg = "Operations cannot declare direct origins"
        raise ValueError(msg)
    if any(
        resource_annotation(annotation) is not None
        for annotation in _handler_hints(node.name, node.function).values()
    ):
        msg = "Operations cannot declare resource bindings"
        raise ValueError(msg)


def _resolve_binding(
    owner: CompiledNode,
    dependency: Dependency | LocationDependency,
    nodes: Mapping[str, CompiledNode],
    providers: Mapping[str, RegisteredProvider],
    predecessors: set[str],
) -> None:
    if isinstance(dependency, LocationDependency) and dependency.provider is not None:
        if dependency.parameters or dependency.locations:
            msg = "Provider requirements do not accept invocation bindings"
            raise ValueError(msg)
        _provider(providers, dependency.provider)
        return
    producer_name = (
        dependency.producer
        if isinstance(dependency, Dependency)
        else dependency.operation
    )
    if producer_name is None or producer_name not in nodes:
        msg = f"Unknown dependency producer {producer_name!r}"
        raise ValueError(msg)
    producer = nodes[producer_name]
    predecessors.add(producer_name)
    if isinstance(dependency, Dependency):
        if (
            producer.declaration.kind != "metric"
            or dependency.output not in producer.declaration.outputs
        ):
            msg = f"Unknown metric output {producer_name!r}/{dependency.output!r}"
            raise ValueError(msg)
    elif producer.declaration.kind != "location_operation":
        msg = f"Expected location operation {producer_name!r}"
        raise ValueError(msg)
    _validate_bindings((owner, producer), dependency, nodes, providers, predecessors)


def _validate_bindings(
    participants: tuple[CompiledNode, CompiledNode],
    dependency: Dependency | LocationDependency,
    nodes: Mapping[str, CompiledNode],
    providers: Mapping[str, RegisteredProvider],
    predecessors: set[str],
) -> None:
    owner, producer = participants
    receiving = {
        key: value
        for key, value in producer.declaration.inputs.items()
        if isinstance(value, LocationDependency) and value.provider is not None
    }
    if dependency.locations.keys() - receiving.keys():
        msg = "Unknown receiving location binding"
        raise ValueError(msg)
    for key in receiving:
        binding = dependency.locations.get(key, Input(key))
        if isinstance(binding, Input):
            source = owner.declaration.inputs.get(binding.path)
            if not isinstance(source, LocationDependency):
                msg = f"No location supply for {key!r}"
                raise TypeError(msg)
        else:
            _resolve_binding(owner, binding, nodes, providers, predecessors)
    fields = producer.parameters.model_fields if producer.parameters else {}
    caller_fields = owner.parameters.model_fields if owner.parameters else {}
    if dependency.parameters.keys() - fields.keys():
        msg = "Unknown receiving parameter binding"
        raise ValueError(msg)
    for value in dependency.parameters.values():
        if isinstance(value, Input) and (
            not value.path.startswith("parameters.")
            or value.path.split(".")[1] not in caller_fields
        ):
            msg = f"Unknown caller parameter reference {value.path!r}"
            raise ValueError(msg)
    for key, parameter in fields.items():
        if (
            key not in dependency.parameters
            and key not in caller_fields
            and parameter.is_required()
        ):
            msg = f"No parameter supply for {key!r}"
            raise ValueError(msg)


def _validate_declaration(original: NodeDeclaration) -> None:
    validate_metric_name(original.name)
    for output in original.outputs:
        validate_metric_name(output)
    if original.kind not in {"metric", "location_operation"}:
        msg = "Unknown node kind"
        raise ValueError(msg)
    if any(
        not isinstance(value, Dependency | LocationDependency)
        for value in original.inputs.values()
    ):
        msg = "Node inputs require live dependency declarations"
        raise TypeError(msg)
    validate_cache(original.cache, original.scope)
