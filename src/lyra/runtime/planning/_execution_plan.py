"""Validate selected route ordering against declared calculation dependencies."""

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from itertools import pairwise

from lyra.runtime.planning.graph import PluginGraph


def validate_execution_plan(
    graph: PluginGraph, node: str, routes: Mapping[str, tuple[str, ...]]
) -> None:
    """Check one selected combination without acquisition or route selection.

    Raises:
        ValueError: If required nodes are unavailable or execution ordering cycles.
    """
    try:
        required = graph.dependency_closure(
            [node, *(operation for route in routes.values() for operation in route)]
        )
    except KeyError as exc:
        message = (
            f"Execution plan for {node!r} requires unavailable node {exc.args[0]!r}"
        )
        raise ValueError(message) from exc
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
