"""Compile enabled contracts without importing handlers or acquiring resources."""

from collections import deque
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType

from lyra.sdk.dependencies import Input
from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import PluginGraph, build_graph, parameter_contract
from lyra.sdk.location_contracts import LocationContract, LocationContractReference
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
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ]


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
    edges = _canonical_edges(graph)
    contracts = [
        contract.reference
        for entry in graph.nodes.values()
        for contract in entry.node.location_contracts.values()
    ]
    contracts.extend(
        entry.node.output_contract.reference
        for entry in graph.nodes.values()
        if entry.node.output_contract is not None
    )
    if selected is not None:
        contracts.extend(c.reference for c in selected.declaration.contracts)
    return CompiledCatalog(
        MappingProxyType(captured),
        graph,
        graph.editions,
        selected,
        edges,
        compile_routes(
            edges,
            contracts,
            (
                route
                for manifest in captured.values()
                for route in manifest.route_preferences
            ),
        ),
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


class AmbiguousRoutesError(ValueError):
    """Shortest-route ties with two deterministic witness sequences per pair."""

    def __init__(
        self,
        ambiguities: Mapping[
            tuple[LocationContractReference, LocationContractReference],
            tuple[tuple[str, ...], ...],
        ],
    ) -> None:
        """Capture bounded witnesses for every ambiguous pair."""
        self.ambiguities = MappingProxyType(dict(ambiguities))
        super().__init__(f"Ambiguous shortest canonical routes: {dict(ambiguities)!r}")


def compile_routes(
    edges: Iterable[CanonicalEdge],
    contracts: Iterable[LocationContractReference] = (),
    preferences: Iterable[tuple[str, ...]] = (),
) -> Mapping[
    tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
]:
    """Compute unit-cost shortest sequences, retaining at most two witnesses.

    Breadth-first traversal completes each preceding distance layer before
    propagating witnesses. Cycles cannot improve distances; parallel operations
    remain distinct choices. Missing pairs are unreachable.

    Returns:
        Immutable routes keyed by concrete source and target identity.

    Raises:
        AmbiguousRoutesError: If any pair has unresolved shortest sequences.
    """
    edges = tuple(edges)
    preferred = _route_preferences(edges, preferences)
    adjacency: dict[LocationContractReference, list[CanonicalEdge]] = {
        contract: [] for contract in contracts
    }
    for edge in sorted(edges, key=lambda edge: edge.operation):
        adjacency.setdefault(edge.source.reference, []).append(edge)
        adjacency.setdefault(edge.target.reference, [])
    routes = {}
    ambiguities = {}
    for source in sorted(adjacency, key=lambda ref: ref.model_dump_json()):
        distances = {source: 0}
        witnesses: dict[LocationContractReference, tuple[tuple[str, ...], ...]] = {
            source: ((),)
        }
        queue = deque([source])
        while queue:
            current = queue.popleft()
            distance = distances[current] + 1
            for edge in adjacency[current]:
                target = edge.target.reference
                if target not in distances:
                    distances[target] = distance
                    witnesses[target] = ()
                    queue.append(target)
                if distances[target] == distance:
                    candidates = set(witnesses[target])
                    candidates.update(
                        (*path, edge.operation) for path in witnesses[current]
                    )
                    witnesses[target] = tuple(sorted(candidates)[:2])
        _validate_preferred_lengths(source, distances, preferred)
        for target, paths in witnesses.items():
            pair = source, target
            if pair in preferred or len(paths) == 1:
                routes[pair] = preferred.get(pair, paths[0])
            else:
                ambiguities[pair] = paths
    if ambiguities:
        raise AmbiguousRoutesError(ambiguities)
    return MappingProxyType(routes)


def _route_preferences(
    edges: Iterable[CanonicalEdge], preferences: Iterable[tuple[str, ...]]
) -> dict[tuple[LocationContractReference, LocationContractReference], tuple[str, ...]]:
    operations = {edge.operation: edge for edge in edges}
    selected = {}
    for declared in preferences:
        sequence = tuple(declared)
        if not sequence:
            message = "Route preferences require a nonempty operation sequence"
            raise ValueError(message)
        try:
            route = [operations[name] for name in sequence]
        except KeyError as exc:
            message = (
                f"Unknown or noncanonical route preference operation: {exc.args[0]!r}"
            )
            raise ValueError(message) from exc
        if any(a.target.reference != b.source.reference for a, b in pairwise(route)):
            message = f"Disconnected route preference: {sequence!r}"
            raise ValueError(message)
        pair = route[0].source.reference, route[-1].target.reference
        if pair in selected and selected[pair] != sequence:
            message = f"Conflicting route preferences for {pair!r}"
            raise ValueError(message)
        selected[pair] = sequence
    return selected


def _validate_preferred_lengths(
    source: LocationContractReference,
    distances: Mapping[LocationContractReference, int],
    preferred: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ],
) -> None:
    for pair, preference in preferred.items():
        if pair[0] == source and len(preference) != distances[pair[1]]:
            message = f"Route preference is not shortest: {preference!r}"
            raise ValueError(message)
