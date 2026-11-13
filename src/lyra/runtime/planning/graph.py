"""Compile executable graph structure from registered live declarations."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import Any

from lyra.contracts.dependencies import (
    Dependency,
    LocationDependency,
)
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import NodeManifest
from lyra.sdk.plugin import NodeImplementation, PluginDefinition


@dataclass(frozen=True)
class NodeEntry:
    """A node contract and its implementation ownership."""

    node: NodeManifest
    distribution: str
    implementation: NodeImplementation | None = field(default=None, compare=False)


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
    dependency: Dependency
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
    dependency: LocationDependency
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


def parameter_contract(node: NodeManifest) -> dict[str, Any]:
    """Return a detached parameter object schema, including its local definitions."""
    return (
        deepcopy(node.parameters_schema) if node.parameters_schema is not None else {}
    )


def build_graph(definitions: Mapping[str, PluginDefinition]) -> PluginGraph:
    """Build edges from captured live inputs after combined declaration validation.

    Returns:
        Executable nodes and caller-scoped dependency edges.

    Raises:
        ValueError: If registered nodes or outputs conflict or are missing.
    """
    nodes = {}
    outputs = {}
    bindings = {}
    for owner, definition in sorted(definitions.items()):
        bindings.update(
            {
                value.name: deepcopy(dict(value.inputs))
                for value in definition.declarations
            }
        )
        for name in definition.node_names:
            if name in nodes:
                msg = f"Duplicate node {name!r}"
                raise ValueError(msg)
            implementation = definition.implementation(name)
            entry = NodeEntry(implementation.manifest, owner, implementation)
            nodes[name] = entry
            if entry.node.kind == "metric":
                for output in implementation.schemas:
                    outputs[name, output] = OutputEntry(
                        entry, entry.node.outputs[output]
                    )
    dependencies: dict[str, dict[str, ResolvedDependency]] = {}
    locations: dict[str, dict[str, ResolvedLocationDependency]] = {}
    predecessors = {}
    for name, entry in nodes.items():
        dependencies[name] = {}
        locations[name] = {}
        for key, binding in bindings[name].items():
            if isinstance(binding, Dependency):
                if (binding.producer, binding.output) not in outputs:
                    msg = (
                        f"Unknown producer/output {binding.producer!r}/"
                        f"{binding.output!r}"
                    )
                    raise ValueError(msg)
                dependencies[name][key] = ResolvedDependency(
                    entry, key, binding, outputs[binding.producer, binding.output]
                )
            _location_edges(entry, binding, f"inputs.{key}", nodes, locations[name])
        predecessors[name] = {
            edge.output.producer.node.name for edge in dependencies[name].values()
        } | {edge.producer.node.name for edge in locations[name].values()}
    return PluginGraph(
        nodes,
        outputs,
        dependencies,
        locations,
        tuple(TopologicalSorter(predecessors).static_order()),
    )


def _location_edges(
    owner: NodeEntry,
    binding: Dependency | LocationDependency,
    path: str,
    nodes: Mapping[str, NodeEntry],
    edges: dict[str, ResolvedLocationDependency],
) -> None:
    if isinstance(binding, LocationDependency) and binding.operation is not None:
        edges[path] = ResolvedLocationDependency(
            owner, path, binding, nodes[binding.operation]
        )
    for key, nested in binding.locations.items():
        if isinstance(nested, LocationDependency):
            _location_edges(owner, nested, f"{path}.locations.{key}", nodes, edges)
