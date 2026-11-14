"""Compile live registrations and their descriptions without acquiring resources."""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from itertools import pairwise
from types import MappingProxyType

from lyra.contracts.accepted import AcceptedSource
from lyra.contracts.dependencies import Input
from lyra.contracts.locations import LocationContract, LocationContractReference
from lyra.contracts.plugins import NodeManifest, PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.computation import CatalogComputation, compile_computation
from lyra.runtime.planning.dependency_routes import compile_dependency_routes
from lyra.runtime.planning.graph import PluginGraph, build_graph, parameter_contract
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.runtime.planning.parameters import prepare_parameter_uses
from lyra.runtime.planning.provider_registry import (
    RegisteredProvider,
    compile_provider_registry,
)
from lyra.runtime.planning.providers import (
    SelectedLocationProvider,
    select_location_providers,
)
from lyra.runtime.planning.revisions import CatalogRevisions, compile_revisions
from lyra.runtime.resources import collect_resources
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import resource_metadata


@dataclass(frozen=True)
class CompiledCatalog:
    """Captured contracts shared by application and local execution."""

    build_revision: str | None = field(default=None, kw_only=True)
    implementations: Mapping[str, BoundPlugin] = field(
        default_factory=dict, kw_only=True
    )
    provider_registry: Mapping[str, RegisteredProvider] = field(
        default_factory=dict, kw_only=True
    )
    dependency_routes: Mapping[tuple[str, str, str], AcceptedSource] = field(
        default_factory=dict, kw_only=True
    )
    computation: CatalogComputation
    revisions: CatalogRevisions
    manifests: Mapping[str, PluginManifest]
    graph: PluginGraph
    providers: Mapping[str, SelectedLocationProvider]
    canonical_edges: tuple["CanonicalEdge", ...]
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ]

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
    definitions = bind_native_definitions(definitions)
    implementations = {name: BoundPlugin(value) for name, value in definitions.items()}
    providers = collect_resources(
        {name: value.resources for name, value in implementations.items()}
    )
    for definition in definitions.values():
        definition.validate_resources(providers)
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
    catalog = replace(
        assemble_catalog(graph, descriptions),
        implementations=MappingProxyType(implementations),
        provider_registry=compile_provider_registry(definitions),
        build_revision=build_revision,
    )
    catalog = replace(
        catalog, dependency_routes=compile_dependency_routes(graph, catalog.routes)
    )
    for edge in catalog.canonical_edges:
        prepare_parameter_uses(catalog, edge.operation, {})
    return catalog


def assemble_catalog(
    graph: PluginGraph, captured: Mapping[str, PluginManifest]
) -> CompiledCatalog:
    """Derive inspection, routing and identity views from the captured graph.

    Returns:
        Catalog retaining the supplied graph as its executable authority.

    Raises:
        ValueError: If a selector target lacks managed query capability.
    """
    selected = select_location_providers(
        {key: value.providers for key, value in captured.items()}
    )
    for entry in graph.nodes.values():
        node = entry.node
        if node.kind == "location_operation" and node.has_target_selector:
            target = selected.get(str(node.target_provider))
            if target is None or not target.declaration.query_capable:
                message = f"Operation {node.name!r} requires managed query capability"
                raise ValueError(message)
    edges = _canonical_edges(graph)
    contracts = [
        contract.reference
        for entry in graph.nodes.values()
        for contract in (
            value.contract for value in entry.node.location_inputs.values()
        )
    ]
    contracts.extend(
        entry.node.output_contract.reference
        for entry in graph.nodes.values()
        if entry.node.kind == "location_operation"
    )
    contracts.extend(p.declaration.contract.reference for p in selected.values())
    routes = compile_routes(
        edges,
        contracts,
        (
            route
            for manifest in captured.values()
            for route in manifest.route_preferences
        ),
    )
    accepted = _accepted_sources(graph, routes)
    revisions = compile_revisions(graph, selected)
    return CompiledCatalog(
        compile_computation(graph, selected),
        revisions,
        MappingProxyType(captured),
        graph,
        selected,
        edges,
        routes,
        accepted,
    )


def _accepted_sources(
    graph: PluginGraph,
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ],
) -> Mapping[str, Mapping[str, Mapping[str, AcceptedSource]]]:
    by_target: dict[LocationContractReference, dict[str, list[AcceptedSource]]] = {}
    for (source, target), operations in routes.items():
        by_target.setdefault(target, {}).setdefault(source.collection, []).append(
            AcceptedSource(source, target, operations)
        )
    result = {}
    for name, entry in graph.nodes.items():
        node = entry.node
        if node.kind != "metric":
            continue
        inputs = {}
        for input_name, declaration in node.location_inputs.items():
            if (
                declaration.dependency is not None
                and declaration.dependency.operation is not None
            ):
                continue
            native = declaration.contract
            candidates = by_target[native.reference]
            overrides = _source_overrides(
                node, input_name, native.reference, candidates
            )
            selections = {}
            for key, choices in candidates.items():
                selected = [
                    choice
                    for choice in choices
                    if key not in overrides or choice.source == overrides[key]
                ]
                if len(selected) != 1:
                    message = (
                        f"Ambiguous source binding on {name!r}/{input_name} "
                        f"for {key!r}: {[choice.source for choice in selected]!r}"
                    )
                    raise ValueError(message)
                selections[key] = selected[0]
            inputs[input_name] = MappingProxyType(selections)
        result[name] = MappingProxyType(inputs)
    return MappingProxyType(result)


def _source_overrides(
    node: NodeManifest,
    input_name: str,
    native: LocationContractReference,
    candidates: Mapping[str, list[AcceptedSource]],
) -> dict[str, LocationContractReference]:
    overrides = {}
    label = f"{node.name!r}/{input_name}"
    for source in node.location_inputs[input_name].source_bindings:
        key = source.collection
        if key in overrides:
            message = f"Duplicate source binding on {label}"
            raise ValueError(message)
        if not any(choice.source == source for choice in candidates.get(key, ())):
            message = f"Unreachable source binding on {label}: {source!r}"
            raise ValueError(message)
        if key == native.collection and source != native:
            message = f"Source binding replaces native selection on {label}"
            raise ValueError(message)
        overrides[key] = source
    return overrides


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
        if node.kind != "location_operation" or not node.canonical:
            continue
        if len(node.location_inputs) != 1:
            message = f"Canonical operation {node.name!r} requires one input"
            raise ValueError(message)
        _validate_canonical_invocation(graph, node, set(), node.name)
        input_name, declaration = next(iter(node.location_inputs.items()))
        edges.append(
            CanonicalEdge(
                node.name, input_name, declaration.contract, node.output_contract
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
