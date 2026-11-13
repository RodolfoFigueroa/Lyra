"""Validate selected route ordering against declared calculation dependencies."""

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from itertools import pairwise

from lyra.contracts.accepted import AcceptedSource
from lyra.runtime.planning.graph import PluginGraph


def validate_execution_plan(
    graph: PluginGraph,
    node: str,
    routes: Mapping[str, tuple[str, ...]],
    dependency_routes: Mapping[tuple[str, str, str], AcceptedSource] | None = None,
) -> tuple[str, ...]:
    """Check one selected combination without acquisition or route selection.

    Returns:
        The complete selected implementation closure.

    Raises:
        ValueError: If required nodes are unavailable or execution ordering cycles.
    """
    try:
        required = _selected_nodes(graph, node, routes, dependency_routes or {})
    except KeyError as exc:
        message = (
            f"Execution plan for {node!r} requires unavailable node {exc.args[0]!r}"
        )
        raise ValueError(message) from exc
    _validate_route_order(graph, node, routes, required)
    groups: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}
    for (owner, path, receiving), route in (dependency_routes or {}).items():
        if owner in required and route.operations:
            groups.setdefault((owner, path), {})[receiving] = route.operations
    for (owner, path), selected in groups.items():
        if path in graph.location_dependencies[owner]:
            receiver = graph.location_dependencies[owner][path].producer.node.name
        else:
            receiver = graph.dependencies[owner][
                path.removeprefix("inputs.")
            ].output.producer.node.name
        _validate_route_order(
            graph,
            receiver,
            selected,
            graph.dependency_closure(
                [receiver, *(op for route in selected.values() for op in route)]
            ),
        )
    return required


def _validate_route_order(
    graph: PluginGraph,
    node: str,
    routes: Mapping[str, tuple[str, ...]],
    required: tuple[str, ...],
) -> None:
    # Input routes interact within a receiving invocation. Independent uses of
    # the same operation can have opposite ordering without forming a cycle.
    predecessors = {
        name: {
            edge.output.producer.node.name for edge in graph.dependencies[name].values()
        }
        | {
            edge.producer.node.name
            for edge in graph.location_dependencies[name].values()
        }
        for name in required
    }
    for route in routes.values():
        for producer, consumer in pairwise((*route, node)):
            predecessors[consumer].add(producer)
    try:
        tuple(
            TopologicalSorter(
                {
                    name: sorted(dependencies)
                    for name, dependencies in predecessors.items()
                }
            ).static_order()
        )
    except CycleError as exc:
        message = (
            f"Execution plan cycle for {node!r}, inputs {tuple(sorted(routes))!r}: "
            f"{exc.args[1]!r}"
        )
        raise ValueError(message) from exc


def _selected_nodes(
    graph: PluginGraph,
    node: str,
    routes: Mapping[str, tuple[str, ...]],
    dependency_routes: Mapping[tuple[str, str, str], AcceptedSource],
) -> tuple[str, ...]:
    required = graph.dependency_closure(
        [node, *(operation for route in routes.values() for operation in route)]
    )
    while True:
        routed = {
            operation
            for (owner, _, _), route in dependency_routes.items()
            if owner in required
            for operation in route.operations
        }
        expanded = graph.dependency_closure([*required, *sorted(routed)])
        if expanded == required:
            return required
        required = expanded
