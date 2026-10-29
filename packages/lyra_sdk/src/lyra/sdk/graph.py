"""Resolve enabled manifest graphs without importing calculation code."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from lyra.sdk.dependencies import DependencyManifest, Input
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
    """A consumer input resolved to its producer and parameter bindings."""

    consumer: NodeEntry
    input_name: str
    dependency: DependencyManifest
    output: OutputEntry

    @property
    def diagnostic(self) -> str:
        """Context shared by structural and execution diagnostics."""
        return (
            f"Node {self.consumer.node.name!r}, input {self.input_name!r}, "
            f"output {self.dependency.output!r}, "
            f"producer {self.output.producer.node.name!r}"
        )


@dataclass(frozen=True)
class PluginGraph:
    """Complete validated nodes, outputs, and resolved receiving inputs."""

    nodes: dict[str, NodeEntry]
    outputs: dict[str, OutputEntry]
    dependencies: dict[str, dict[str, ResolvedDependency]]


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


def _validate_edge(edge: ResolvedDependency) -> None:
    consumer = edge.consumer.node
    producer = edge.output.producer.node
    source = parameter_contract(consumer).get("properties", {})
    target = parameter_contract(producer)
    fields = target.get("properties", {})
    overrides = edge.dependency.arguments.get("parameters", {})
    errors = []
    for name, value in overrides.items():
        if name not in fields:
            errors.append(f"unknown parameter override {name!r}")
        if isinstance(value, Input) and value.path.split(".")[1] not in source:
            errors.append(f"unknown consumer parameter {value.path!r}")
    errors.extend(
        f"no supply path for required parameter {name!r}"
        for name in target.get("required", [])
        if name not in overrides and name not in source
    )
    for name, kind in producer.spatial_inputs.items():
        if consumer.spatial_inputs.get(name) != kind:
            errors.append(f"no matching spatial input {name!r} of kind {kind!r}")
    if errors:
        msg = f"{edge.diagnostic}: {'; '.join(errors)}"
        raise ValueError(msg)


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


def _resolve_graph(
    nodes: dict[str, NodeEntry], outputs: dict[str, OutputEntry]
) -> PluginGraph:
    dependencies: dict[str, dict[str, ResolvedDependency]] = {}
    for name, entry in nodes.items():
        dependencies[name] = {}
        for input_name, dependency in entry.node.inputs.items():
            if dependency.output not in outputs:
                msg = (
                    f"Node {name!r}, input {input_name!r}, "
                    f"output {dependency.output!r}: no enabled producer"
                )
                raise ValueError(msg)
            edge = ResolvedDependency(
                entry, input_name, dependency, outputs[dependency.output]
            )
            _validate_edge(edge)
            dependencies[name][input_name] = edge
    predecessors = {
        name: {edge.output.producer.node.name for edge in edges.values()}
        for name, edges in dependencies.items()
    }
    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        details = [
            edge.diagnostic
            for consumer, producer in zip(cycle[1:], cycle[:-1], strict=True)
            for edge in dependencies[consumer].values()
            if edge.output.producer.node.name == producer
        ]
        msg = "Dependency cycle: " + "; ".join(details)
        raise ValueError(msg) from exc
    return PluginGraph(nodes, outputs, dependencies)
