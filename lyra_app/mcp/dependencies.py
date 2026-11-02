"""Bounded projections of declared calculation relationships."""

from heapq import nsmallest

from lyra.sdk.discovery import Discovery
from lyra.sdk.models.plugin import NodeManifest

from lyra_app.mcp.models import (
    DependencyNode,
    GetMetricInput,
    MetricDependenciesOutput,
    MetricRelationship,
)


def _node(node: NodeManifest) -> DependencyNode:
    return DependencyNode(
        name=node.name,
        kind=node.kind,
        family=node.family,
        edition=node.edition,
        private=node.private,
        description=node.description[:240],
    )


def inspect_dependencies(
    discovery: Discovery,
    arguments: GetMetricInput,
) -> MetricDependenciesOutput:
    """Project only incident declared edges of a public concrete metric.

    Returns:
        At most fifty relationships ordered by direction and concrete identifiers.

    """
    catalog = discovery.catalog
    discovery.inspect(arguments.metric, edition=arguments.edition)
    name = catalog.editions.resolve(arguments.metric, edition=arguments.edition)
    graph = catalog.graph
    edges: list[tuple[str, str, str, str]] = []
    for consumer, dependencies in graph.dependencies.items():
        for receiving, edge in dependencies.items():
            producer = edge.output.producer.node.name
            if name in {consumer, producer}:
                edges.append((consumer, producer, receiving, edge.dependency.output))
    for consumer, locations in graph.location_dependencies.items():
        for receiving, location in locations.items():
            producer = location.producer.node.name
            if name in {consumer, producer}:
                edges.append((consumer, producer, receiving, ""))
    selected = nsmallest(
        50,
        edges,
        key=lambda edge: (edge[0] != name, edge[1], edge[0], edge[2], edge[3]),
    )
    return MetricDependenciesOutput(
        metric=_node(graph.nodes[name].node),
        relationships=[
            MetricRelationship(
                direction="dependency" if consumer == name else "consumer",
                producer=_node(graph.nodes[producer].node),
                consumer=_node(graph.nodes[consumer].node),
                producer_output=output or None,
                consumer_input=receiving,
            )
            for consumer, producer, receiving, output in selected
        ],
        omitted_relationships=len(edges) - len(selected),
    )
