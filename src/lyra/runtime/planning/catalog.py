"""Compile live registrations and their descriptions without acquiring resources."""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType

from lyra.contracts.accepted import AcceptedSource
from lyra.contracts.dependencies import Input
from lyra.contracts.plugins import NodeManifest, PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.computation import CatalogComputation, compile_computation
from lyra.runtime.planning.dependency_routes import compile_dependency_routes
from lyra.runtime.planning.graph import PluginGraph, build_graph, parameter_contract
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.runtime.planning.provider_registry import (
    RegisteredProvider,
    compile_registrations,
)
from lyra.runtime.planning.providers import (
    SelectedLocationProvider,
    select_location_providers,
)
from lyra.runtime.planning.revisions import CatalogRevisions, compile_revisions
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import BaseIO, resource_metadata


@dataclass(frozen=True, kw_only=True)
class CompiledCatalog:
    """Captured contracts shared by application and local execution."""

    build_revision: str | None = None
    resources: Mapping[str, type[BaseIO]]
    implementations: Mapping[str, BoundPlugin]
    provider_registry: Mapping[str, RegisteredProvider]
    dependency_routes: Mapping[tuple[str, str, str], AcceptedSource]
    computation: CatalogComputation
    revisions: CatalogRevisions
    manifests: Mapping[str, PluginManifest]
    graph: PluginGraph
    providers: Mapping[str, SelectedLocationProvider]
    canonical_edges: tuple["CanonicalEdge", ...]
    routes: Mapping[tuple[str, str], tuple[str, ...]]

    sources_by_target: Mapping[str, Mapping[str, AcceptedSource]]
    accepted_sources: Mapping[str, Mapping[str, Mapping[str, "AcceptedSource"]]]


def compile_live_catalog(
    definitions: Mapping[str, PluginDefinition],
    *,
    build_revision: str | None = None,
) -> CompiledCatalog:
    """Compile live registrations and retain their executable implementations.

    Returns:
        Validated catalog with serializable descriptions and live bindings.
    """
    resources, registry = compile_registrations(definitions)
    definitions = bind_native_definitions(definitions, registry)
    implementations = {name: BoundPlugin(value) for name, value in definitions.items()}
    graph = build_graph(definitions)
    descriptions = {
        name: PluginManifest.model_construct(
            plugin=PluginInfo(name=name, version="live"),
            factory="live:create_plugin",
            nodes=list(definition.node_manifests),
            resources={
                key: resource_metadata(provider)
                for key, provider in definition.resources.items()
            },
            providers=definition.providers,
            route_preferences=definition.route_preferences,
        )
        for name, definition in definitions.items()
    }
    selected = select_location_providers(registry, definitions)
    edges = _canonical_edges(graph)
    routes = compile_routes(
        edges,
        selected,
        (
            route
            for manifest in descriptions.values()
            for route in manifest.route_preferences
        ),
    )
    sources_by_target = _sources_by_target(routes)
    return CompiledCatalog(
        computation=compile_computation(graph, selected),
        revisions=compile_revisions(graph, selected),
        manifests=MappingProxyType(descriptions),
        graph=graph,
        providers=selected,
        canonical_edges=edges,
        routes=routes,
        sources_by_target=sources_by_target,
        accepted_sources=_accepted_sources(graph, sources_by_target),
        resources=resources,
        implementations=MappingProxyType(implementations),
        provider_registry=registry,
        dependency_routes=compile_dependency_routes(graph, routes),
        build_revision=build_revision,
    )


def _sources_by_target(
    routes: Mapping[tuple[str, str], tuple[str, ...]],
) -> Mapping[str, Mapping[str, AcceptedSource]]:
    by_target: dict[str, dict[str, AcceptedSource]] = {}
    for (source, target), operations in sorted(routes.items()):
        by_target.setdefault(target, {})[source] = AcceptedSource(
            source, target, operations
        )
    return MappingProxyType(
        {target: MappingProxyType(sources) for target, sources in by_target.items()}
    )


def _accepted_sources(
    graph: PluginGraph, by_target: Mapping[str, Mapping[str, AcceptedSource]]
) -> Mapping[str, Mapping[str, Mapping[str, AcceptedSource]]]:
    return MappingProxyType(
        {
            name: MappingProxyType(
                {
                    key: by_target[value.contract.provider_scope.provider_id]
                    for key, value in entry.node.location_inputs.items()
                    if value.dependency is None or value.dependency.operation is None
                }
            )
            for name, entry in graph.nodes.items()
            if entry.node.kind == "metric"
        }
    )


@dataclass(frozen=True)
class CanonicalEdge:
    """A canonical operation connecting two registered providers."""

    operation: str
    source: str
    target: str


def _canonical_edges(graph: PluginGraph) -> tuple[CanonicalEdge, ...]:
    edges = []
    for entry in graph.nodes.values():
        node = entry.node
        if node.kind != "location_operation" or not node.canonical:
            continue
        if len(node.location_inputs) != 1:
            message = f"Canonical operation {node.name!r} requires one input"
            raise ValueError(message)
        _validate_canonical_invocation(graph, node, set(), node.name)
        declaration = next(iter(node.location_inputs.values()))
        edges.append(
            CanonicalEdge(
                node.name,
                declaration.contract.provider_scope.provider_id,
                node.output_contract.provider_scope.provider_id,
            )
        )
    return tuple(edges)


def _validate_canonical_invocation(
    graph: PluginGraph, node: NodeManifest, supplied: set[str], root: str
) -> None:
    schema = parameter_contract(node)
    available = supplied | (
        set(schema.get("properties", {})) - set(schema.get("required", []))
    )
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
            tuple[str, str],
            tuple[tuple[str, ...], ...],
        ],
    ) -> None:
        """Capture bounded witnesses for every ambiguous pair."""
        self.ambiguities = MappingProxyType(dict(ambiguities))
        super().__init__(f"Ambiguous shortest canonical routes: {dict(ambiguities)!r}")


def compile_routes(
    edges: Iterable[CanonicalEdge],
    providers: Iterable[str] = (),
    preferences: Iterable[tuple[str, ...]] = (),
) -> Mapping[tuple[str, str], tuple[str, ...]]:
    """Compute unit-cost shortest sequences, retaining at most two witnesses.

    Breadth-first traversal completes each preceding distance layer before
    propagating witnesses. Cycles cannot improve distances; parallel operations
    remain distinct choices. Missing pairs are unreachable.

    Returns:
        Immutable routes keyed by source and target provider IDs.

    Raises:
        AmbiguousRoutesError: If any pair has unresolved shortest sequences.
    """
    edges = tuple(edges)
    preferred = _route_preferences(edges, preferences)
    adjacency: dict[str, list[CanonicalEdge]] = {provider: [] for provider in providers}
    for edge in sorted(edges, key=lambda edge: edge.operation):
        adjacency.setdefault(edge.source, []).append(edge)
        adjacency.setdefault(edge.target, [])
    routes = {}
    ambiguities = {}
    for source in sorted(adjacency):
        distances = {source: 0}
        witnesses: dict[str, tuple[tuple[str, ...], ...]] = {source: ((),)}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            distance = distances[current] + 1
            for edge in adjacency[current]:
                target = edge.target
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
) -> dict[tuple[str, str], tuple[str, ...]]:
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
        if any(a.target != b.source for a, b in pairwise(route)):
            message = f"Disconnected route preference: {sequence!r}"
            raise ValueError(message)
        pair = route[0].source, route[-1].target
        if pair in selected and selected[pair] != sequence:
            message = f"Conflicting route preferences for {pair!r}"
            raise ValueError(message)
        selected[pair] = sequence
    return selected


def _validate_preferred_lengths(
    source: str,
    distances: Mapping[str, int],
    preferred: Mapping[tuple[str, str], tuple[str, ...]],
) -> None:
    for pair, preference in preferred.items():
        if pair[0] == source and len(preference) != distances[pair[1]]:
            message = f"Route preference is not shortest: {preference!r}"
            raise ValueError(message)
