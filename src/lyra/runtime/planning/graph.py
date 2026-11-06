"""Resolve enabled manifest graphs without importing calculation code."""

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
from graphlib import CycleError, TopologicalSorter
from typing import Any

from lyra.contracts.dependencies import (
    DependencyManifest,
    Input,
    LocationDependencyManifest,
)
from lyra.contracts.editions import EditionRegistry
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import NodeManifest
from lyra.contracts.schema import check_parameter_schema


@dataclass(frozen=True)
class NodeEntry:
    """A node contract and its implementation ownership."""

    node: NodeManifest
    distribution: str


@dataclass(frozen=True)
class OutputEntry:
    """A named table and the single node that produces it."""

    producer: NodeEntry
    declaration: OutputMetadata


@dataclass(frozen=True)
class ResolvedDependency:
    """A consumer input resolved to its producer and input bindings."""

    consumer: NodeEntry
    input_name: str
    dependency: DependencyManifest
    output: OutputEntry

    @property
    def diagnostic(self) -> str:
        """Context shared by structural and execution diagnostics."""
        return (
            f"Node {self.consumer.node.name!r} ({self.consumer.node.kind}), "
            f"input {self.input_name!r}, "
            f"output {self.dependency.output!r}, "
            f"producer {self.output.producer.node.name!r}"
        )


@dataclass(frozen=True)
class ResolvedLocationDependency:
    """An operation invocation owned by a node, with its full receiving path."""

    consumer: NodeEntry
    binding_path: str
    dependency: LocationDependencyManifest
    producer: NodeEntry

    @property
    def diagnostic(self) -> str:
        """Identify the declaration owner, receiving binding, and operation."""
        return (
            f"Node {self.consumer.node.name!r} ({self.consumer.node.kind}), "
            f"binding {self.binding_path!r}, "
            f"operation {self.dependency.operation!r}, "
            f"producer {self.producer.node.name!r}"
        )


@dataclass(frozen=True)
class PluginGraph:
    """Complete validated nodes, outputs, and resolved receiving inputs."""

    nodes: dict[str, NodeEntry]
    outputs: dict[tuple[str, str], OutputEntry]
    dependencies: dict[str, dict[str, ResolvedDependency]]
    location_dependencies: dict[str, dict[str, ResolvedLocationDependency]]
    order: tuple[str, ...]

    def dependency_closure(self, roots: Iterable[str]) -> tuple[str, ...]:
        """Return sorted roots and all declared table and location dependencies.

        Raises:
            KeyError: If a root is not in this graph.
        """
        pending = list(roots)
        visited: set[str] = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            if name not in self.nodes:
                raise KeyError(name)
            visited.add(name)
            pending.extend(
                edge.output.producer.node.name
                for edge in self.dependencies[name].values()
            )
            pending.extend(
                edge.producer.node.name
                for edge in self.location_dependencies[name].values()
            )
        return tuple(sorted(visited))

    @cached_property
    def editions(self) -> EditionRegistry:
        """Resolve metric families across all enabled plugin registrations."""
        return EditionRegistry([entry.node for entry in self.nodes.values()])


def parameter_contract(node: NodeManifest) -> dict[str, Any]:
    """Return a detached parameter object schema, including its local definitions."""
    return (
        deepcopy(node.parameters_schema) if node.parameters_schema is not None else {}
    )


def _validate_bindings(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: DependencyManifest | LocationDependencyManifest,
    diagnostic: str,
) -> None:
    source = parameter_contract(consumer).get("properties", {})
    target = parameter_contract(producer)
    fields = target.get("properties", {})
    overrides = dependency.parameters
    errors = []
    for name, value in overrides.items():
        if name not in fields:
            errors.append(f"unknown parameter override {name!r}")
        if isinstance(value, Input):
            if not value.path.startswith("parameters."):
                errors.append(
                    f"parameter {name!r} requires a parameter reference, "
                    f"got {value.path!r}"
                )
            elif value.path.split(".")[1] not in source:
                errors.append(f"unknown consumer parameter {value.path!r}")
    errors.extend(
        f"no supply path for required parameter {name!r}"
        for name in target.get("required", [])
        if name not in overrides and name not in source
    )
    errors.extend(_location_binding_errors(consumer, producer, dependency))
    if errors:
        msg = f"{diagnostic}: {'; '.join(errors)}"
        raise ValueError(msg)


def _location_binding_errors(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: DependencyManifest | LocationDependencyManifest,
) -> list[str]:
    errors: list[str] = []
    locations = dependency.locations
    for name, value in locations.items():
        if name not in producer.location_inputs:
            errors.append(f"unknown location override {name!r}")
        if isinstance(value, Input):
            if value.path.startswith("parameters."):
                errors.append(
                    f"location {name!r} requires a location reference, "
                    f"got {value.path!r}"
                )
            elif value.path not in consumer.location_inputs:
                errors.append(f"unknown consumer location {value.path!r}")
    errors.extend(
        f"no supply path for required spatial input {name!r}"
        for name in producer.location_inputs
        if name not in locations and name not in consumer.location_inputs
    )
    errors.extend(_contract_errors(consumer, producer, dependency))
    return errors


def _contract_errors(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: DependencyManifest | LocationDependencyManifest,
) -> list[str]:
    errors: list[str] = []
    locations = dependency.locations
    for name, declaration in producer.location_inputs.items():
        binding = locations.get(name)
        source_name = binding.path if isinstance(binding, Input) else name
        source = consumer.location_inputs.get(source_name)
        if (binding is None or isinstance(binding, Input)) and (
            source is None or source.contract != declaration.contract
        ):
            errors.append(
                f"incompatible location contract for receiving input {name!r}"
            )
    return errors


def build_graph(contracts: Mapping[str, Sequence[NodeManifest]]) -> PluginGraph:
    """Validate and resolve all supplied node contracts atomically.

    Returns:
        The complete graph; no implementation modules are imported.

    Raises:
        ValueError: If names, dependency supply paths, or cycles are invalid.
    """
    nodes: dict[str, NodeEntry] = {}
    outputs: dict[tuple[str, str], OutputEntry] = {}
    for distribution, declarations in sorted(contracts.items()):
        for node in declarations:
            if node.name in nodes:
                msg = (
                    f"Duplicate node {node.name!r} in {distribution!r} and "
                    f"{nodes[node.name].distribution!r}"
                )
                raise ValueError(msg)
            entry = NodeEntry(node, distribution)
            nodes[node.name] = entry
            if node.parameters_schema is not None:
                check_parameter_schema(node.parameters_schema)
            for name, declaration in (
                node.outputs.items() if node.kind == "metric" else ()
            ):
                outputs[node.name, name] = OutputEntry(entry, declaration)
    graph = _resolve_graph(nodes, outputs)
    _ = graph.editions
    return graph


def _resolve_locations(
    owner: NodeEntry,
    dependency: DependencyManifest | LocationDependencyManifest,
    path: str,
    nodes: dict[str, NodeEntry],
    edges: dict[str, ResolvedLocationDependency],
) -> None:
    receiver = nodes[
        dependency.producer
        if isinstance(dependency, DependencyManifest)
        else dependency.operation
    ].node
    for receiving, binding in dependency.locations.items():
        if isinstance(binding, Input):
            continue
        binding_path = f"{path}.locations.{receiving}"
        context = (
            f"Node {owner.node.name!r} ({owner.node.kind}), binding {binding_path!r}, "
            f"operation {binding.operation!r}"
        )
        producer = nodes.get(binding.operation)
        if producer is None:
            reason = (
                "expected a location operation, got a table output"
                if any(
                    binding.operation in entry.node.outputs
                    for entry in nodes.values()
                    if entry.node.kind == "metric"
                )
                else "no enabled producer"
            )
            msg = f"{context}: {reason}"
            raise ValueError(msg)
        if producer.node.kind != "location_operation":
            msg = f"{context}: expected a location operation, got a metric"
            raise ValueError(msg)
        receiving_input = receiver.location_inputs.get(receiving)
        if (
            receiving_input is None
            or producer.node.output_contract != receiving_input.contract
        ):
            msg = (
                f"{context}: incompatible location contract "
                f"for receiving input {receiving!r}"
            )
            raise ValueError(msg)
        edge = ResolvedLocationDependency(owner, binding_path, binding, producer)
        _validate_bindings(owner.node, producer.node, binding, edge.diagnostic)
        edges[binding_path] = edge
        _resolve_locations(owner, binding, binding_path, nodes, edges)


def _resolve_graph(
    nodes: dict[str, NodeEntry], outputs: dict[tuple[str, str], OutputEntry]
) -> PluginGraph:
    dependencies: dict[str, dict[str, ResolvedDependency]] = {}
    locations: dict[str, dict[str, ResolvedLocationDependency]] = {}
    predecessors: dict[str, set[str]] = {}
    for name, entry in nodes.items():
        dependencies[name] = {}
        locations[name] = {}
        for input_name, dependency in entry.node.inputs.items():
            if (dependency.producer, dependency.output) not in outputs:
                reason = (
                    "expected a table output, got a location operation"
                    if dependency.producer in nodes
                    and nodes[dependency.producer].node.kind == "location_operation"
                    else f"no enabled producer/output {dependency.producer!r}/"
                    f"{dependency.output!r}"
                )
                msg = (
                    f"Node {name!r} ({entry.node.kind}), input {input_name!r}, "
                    f"output {dependency.output!r}: {reason}"
                )
                raise ValueError(msg)
            edge = ResolvedDependency(
                entry,
                input_name,
                dependency,
                outputs[dependency.producer, dependency.output],
            )
            _validate_bindings(
                entry.node, edge.output.producer.node, dependency, edge.diagnostic
            )
            dependencies[name][input_name] = edge
            _resolve_locations(
                entry,
                dependency,
                f"inputs.{input_name}",
                nodes,
                locations[name],
            )
        predecessors[name] = {
            edge.output.producer.node.name for edge in dependencies[name].values()
        } | {edge.producer.node.name for edge in locations[name].values()}
    try:
        order = tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        details = []
        for consumer, producer in zip(cycle[1:], cycle[:-1], strict=True):
            details.extend(
                edge.diagnostic
                for edge in dependencies[consumer].values()
                if edge.output.producer.node.name == producer
            )
            details.extend(
                edge.diagnostic
                for edge in locations[consumer].values()
                if edge.producer.node.name == producer
            )
        msg = "Dependency cycle: " + "; ".join(details)
        raise ValueError(msg) from exc
    return PluginGraph(nodes, outputs, dependencies, locations, order)
