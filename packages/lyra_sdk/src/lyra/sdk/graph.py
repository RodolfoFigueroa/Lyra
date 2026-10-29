"""Resolve enabled manifest graphs without importing calculation code."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from lyra.sdk.dependencies import DependencyManifest, Input, LocationDependencyManifest
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import NodeManifest
from lyra.sdk.schema import resolve_schema_reference


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
    outputs: dict[str, OutputEntry]
    dependencies: dict[str, dict[str, ResolvedDependency]]
    location_dependencies: dict[str, dict[str, ResolvedLocationDependency]]
    order: tuple[str, ...]


def parameter_contract(node: NodeManifest) -> dict[str, Any]:
    """Return direct parameter fields and requiredness from the request contract.

    Raises:
        ValueError: If the parameter contract is unsupported or references cycle.
        TypeError: If the parameter schema is not an object.
    """
    root = node.request_schema
    properties = root.get("properties", {})
    schema: Any = (
        properties.get("parameters", {}) if isinstance(properties, dict) else {}
    )
    seen: set[str] = set()
    while isinstance(schema, dict) and "$ref" in schema:
        reference = schema["$ref"]
        if reference in seen:
            msg = f"Node {node.name!r}: cyclic parameter schema reference"
            raise ValueError(msg)
        seen.add(reference)
        target = resolve_schema_reference(root, reference)
        if target is True or target is False:
            msg = f"Node {node.name!r}: parameters must describe an object"
            raise ValueError(msg)
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    if not isinstance(schema, dict):
        msg = f"Node {node.name!r}: parameters must describe an object"
        raise TypeError(msg)
    if schema and (schema.get("type") != "object" or "properties" not in schema):
        msg = f"Node {node.name!r}: parameters must describe an object"
        raise ValueError(msg)
    return schema


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
        if name not in producer.spatial_inputs:
            errors.append(f"unknown location override {name!r}")
        if isinstance(value, Input):
            if value.path.startswith("parameters."):
                errors.append(
                    f"location {name!r} requires a location reference, "
                    f"got {value.path!r}"
                )
            elif value.path not in consumer.spatial_inputs:
                errors.append(f"unknown consumer location {value.path!r}")
    for name, kind in producer.spatial_inputs.items():
        if name not in locations and consumer.spatial_inputs.get(name) != kind:
            errors.append(
                f"no supply path for required spatial input {name!r} of kind {kind!r}"
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
    outputs: dict[str, OutputEntry] = {}
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
            parameter_contract(node)
            for name, declaration in node.outputs.items():
                if name in outputs:
                    msg = (
                        f"Duplicate output {name!r} from node {node.name!r} and "
                        f"{outputs[name].producer.node.name!r}"
                    )
                    raise ValueError(msg)
                outputs[name] = OutputEntry(entry, declaration)
    for name, output in outputs.items():
        if name in nodes and output.producer is not nodes[name]:
            msg = (
                f"Output {name!r} from {output.producer.node.name!r} "
                "collides with another node"
            )
            raise ValueError(msg)
    return _resolve_graph(nodes, outputs)


def _resolve_locations(
    owner: NodeEntry,
    dependency: DependencyManifest | LocationDependencyManifest,
    path: str,
    nodes: dict[str, NodeEntry],
    edges: dict[str, ResolvedLocationDependency],
) -> None:
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
                    binding.operation in entry.node.outputs for entry in nodes.values()
                )
                else "no enabled producer"
            )
            msg = f"{context}: {reason}"
            raise ValueError(msg)
        if producer.node.kind != "location_operation":
            msg = f"{context}: expected a location operation, got a metric"
            raise ValueError(msg)
        edge = ResolvedLocationDependency(owner, binding_path, binding, producer)
        _validate_bindings(owner.node, producer.node, binding, edge.diagnostic)
        edges[binding_path] = edge
        _resolve_locations(owner, binding, binding_path, nodes, edges)


def _resolve_graph(
    nodes: dict[str, NodeEntry], outputs: dict[str, OutputEntry]
) -> PluginGraph:
    dependencies: dict[str, dict[str, ResolvedDependency]] = {}
    locations: dict[str, dict[str, ResolvedLocationDependency]] = {}
    predecessors: dict[str, set[str]] = {}
    for name, entry in nodes.items():
        dependencies[name] = {}
        locations[name] = {}
        for input_name, dependency in entry.node.inputs.items():
            if dependency.output not in outputs:
                reason = (
                    "expected a table output, got a location operation"
                    if dependency.output in nodes
                    and nodes[dependency.output].node.kind == "location_operation"
                    else "no enabled producer"
                )
                msg = (
                    f"Node {name!r} ({entry.node.kind}), input {input_name!r}, "
                    f"output {dependency.output!r}: {reason}"
                )
                raise ValueError(msg)
            edge = ResolvedDependency(
                entry, input_name, dependency, outputs[dependency.output]
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
