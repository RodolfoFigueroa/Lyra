"""Compile enabled contracts without importing handlers or acquiring resources."""

from collections import deque
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from itertools import pairwise, product
from types import MappingProxyType

from lyra.sdk.accepted import AcceptedSource
from lyra.sdk.computation import CatalogComputation, compile_computation
from lyra.sdk.dependencies import Input
from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import PluginGraph, build_graph, parameter_contract
from lyra.sdk.location_contracts import LocationContract, LocationContractReference
from lyra.sdk.locations import SelectedLocationProvider, select_location_provider
from lyra.sdk.models.plugin import NodeManifest, PluginManifest
from lyra.sdk.revisions import CatalogRevisions, compile_revisions


@dataclass(frozen=True)
class CompiledCatalog:
    """Captured contracts shared by application and local execution."""

    computation: CatalogComputation
    revisions: CatalogRevisions
    manifests: Mapping[str, PluginManifest]
    graph: PluginGraph
    editions: EditionRegistry
    location_provider: SelectedLocationProvider | None
    canonical_edges: tuple["CanonicalEdge", ...]
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ]

    accepted_sources: Mapping[
        str, Mapping[str, Mapping[tuple[str, str], "AcceptedSource"]]
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
        for contract in (
            value.contract for value in entry.node.location_inputs.values()
        )
    ]
    contracts.extend(
        entry.node.output_contract.reference
        for entry in graph.nodes.values()
        if entry.node.kind == "location_operation"
    )
    if selected is not None:
        contracts.extend(c.reference for c in selected.declaration.contracts)
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
    _validate_execution_plans(graph, routes, accepted)
    revisions = compile_revisions(graph, selected)
    return CompiledCatalog(
        compile_computation(graph, selected, revisions),
        revisions,
        MappingProxyType(captured),
        graph,
        graph.editions,
        selected,
        edges,
        routes,
        accepted,
    )


def _validate_execution_plans(
    graph: PluginGraph,
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ],
    accepted: Mapping[str, Mapping[str, Mapping[tuple[str, str], "AcceptedSource"]]],
) -> None:
    predecessors = {
        name: {
            edge.output.producer.node.name for edge in graph.dependencies[name].values()
        }
        | {
            edge.producer.node.name
            for edge in graph.location_dependencies[name].values()
        }
        for name in graph.nodes
    }

    def validate(sequences: tuple[tuple[str, ...], ...], owner: str) -> None:
        plan = {name: set(dependencies) for name, dependencies in predecessors.items()}
        for sequence in sequences:
            for producer, consumer in pairwise(sequence):
                plan[consumer].add(producer)
        try:
            tuple(TopologicalSorter(plan).static_order())
        except CycleError as exc:
            message = f"Execution plan cycle for {owner}: {exc.args[1]!r}"
            raise ValueError(message) from exc

    for route in routes.values():
        validate((route,), f"canonical route {route!r}")
    for name, inputs in accepted.items():
        choices = [
            {(*binding.operations, name) for binding in sources.values()}
            for sources in inputs.values()
        ]
        for sequences in product(*choices):
            validate(sequences, f"{name!r}, inputs {tuple(inputs)!r}")


def _accepted_sources(
    graph: PluginGraph,
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ],
) -> Mapping[str, Mapping[str, Mapping[tuple[str, str], AcceptedSource]]]:
    by_target: dict[
        LocationContractReference, dict[tuple[str, str], list[AcceptedSource]]
    ] = {}
    for (source, target), operations in routes.items():
        by_target.setdefault(target, {}).setdefault(
            (source.collection, source.unit_type), []
        ).append(AcceptedSource(source, target, operations))
    result = {}
    for name, entry in graph.nodes.items():
        node = entry.node
        if node.kind != "metric":
            continue
        inputs = {}
        for input_name, declaration in node.location_inputs.items():
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
        if not node.private:
            result[name] = MappingProxyType(inputs)
    return MappingProxyType(result)


def _source_overrides(
    node: NodeManifest,
    input_name: str,
    native: LocationContractReference,
    candidates: Mapping[tuple[str, str], list[AcceptedSource]],
) -> dict[tuple[str, str], LocationContractReference]:
    overrides = {}
    label = f"{node.name!r}/{input_name}"
    for source in node.location_inputs[input_name].source_bindings:
        key = source.collection, source.unit_type
        if key in overrides:
            message = f"Duplicate source binding on {label}"
            raise ValueError(message)
        if not any(choice.source == source for choice in candidates.get(key, ())):
            message = f"Unreachable source binding on {label}: {source!r}"
            raise ValueError(message)
        if key == (native.collection, native.unit_type) and source != native:
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
