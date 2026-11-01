"""Compile enabled contracts without importing handlers or acquiring resources."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from lyra.sdk.dependencies import Input
from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import PluginGraph, build_graph, parameter_contract
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.locations import SelectedLocationProvider, select_location_provider
from lyra.sdk.models.plugin import NodeManifest, PluginManifest


@dataclass(frozen=True)
class CompiledCatalog:
    """Captured contracts shared by application and local execution."""

    manifests: Mapping[str, PluginManifest]
    graph: PluginGraph
    editions: EditionRegistry
    location_provider: SelectedLocationProvider | None
    canonical_edges: tuple["CanonicalEdge", ...]


def compile_catalog(manifests: Mapping[str, PluginManifest]) -> CompiledCatalog:
    """Capture and validate the complete enabled catalog without acquisition.

    Returns:
        Reusable metadata detached from caller-owned declarations.

    Raises:
        ValueError: If enabled declarations conflict or references are invalid.
    """
    captured = deepcopy(dict(manifests))
    resources = {}
    for manifest in captured.values():
        for name, declaration in manifest.resources.items():
            if name in resources and resources[name] != declaration:
                message = f"Conflicting resource declarations: {name}"
                raise ValueError(message)
            resources[name] = declaration
    graph = build_graph({key: value.nodes for key, value in captured.items()})
    selected = select_location_provider(
        {key: value.location_provider for key, value in captured.items()}
    )
    return CompiledCatalog(
        MappingProxyType(captured),
        graph,
        graph.editions,
        selected,
        _canonical_edges(graph),
    )


@dataclass(frozen=True)
class CanonicalEdge:
    """An eligible operation connecting two concrete location contracts."""

    operation: str
    input_name: str
    source: LocationContract
    target: LocationContract


def _canonical_edges(graph: PluginGraph) -> tuple[CanonicalEdge, ...]:
    edges = []
    for entry in graph.nodes.values():
        node = entry.node
        if not node.canonical:
            continue
        if node.kind != "location_operation" or len(node.location_contracts) != 1:
            message = f"Canonical operation {node.name!r} requires one input"
            raise ValueError(message)
        if node.output_contract is None:
            message = f"Canonical operation {node.name!r} requires an output"
            raise ValueError(message)
        _validate_canonical_invocation(graph, node, set(), node.name)
        input_name, source = next(iter(node.location_contracts.items()))
        edges.append(CanonicalEdge(node.name, input_name, source, node.output_contract))
    return tuple(edges)


def _validate_canonical_invocation(
    graph: PluginGraph, node: NodeManifest, supplied: set[str], root: str
) -> None:
    schema = parameter_contract(node)
    available = supplied | {
        name
        for name, field in schema.get("properties", {}).items()
        if isinstance(field, dict) and "default" in field
    }
    missing = set(schema.get("required", [])) - available
    if missing:
        message = (
            f"Canonical operation {root!r}, dependency {node.name!r}: "
            f"unsatisfied parameters {sorted(missing)!r}"
        )
        raise ValueError(message)
    bindings = [
        (edge.output.producer.node, edge.dependency)
        for edge in graph.dependencies[node.name].values()
    ] + [
        (edge.producer.node, edge.dependency)
        for edge in graph.location_dependencies[node.name].values()
    ]
    for producer, binding in bindings:
        forwarded = available & set(parameter_contract(producer).get("properties", {}))
        for name, value in binding.parameters.items():
            if isinstance(value, Input) and value.path.split(".")[1] not in available:
                message = (
                    f"Canonical operation {root!r}, dependency {producer.name!r}: "
                    f"unsatisfied binding {value.path!r}"
                )
                raise ValueError(message)
            forwarded.add(name)
        _validate_canonical_invocation(graph, producer, forwarded, root)
