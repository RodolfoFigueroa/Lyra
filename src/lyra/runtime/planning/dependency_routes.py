"""Resolve canonical routes at each declared receiving location input."""

from collections.abc import Mapping
from types import MappingProxyType

from lyra.contracts.accepted import AcceptedSource
from lyra.contracts.dependencies import Input
from lyra.contracts.locations import LocationContractReference
from lyra.runtime.planning.graph import PluginGraph, ResolvedDependency


def compile_dependency_routes(
    graph: PluginGraph,
    routes: Mapping[
        tuple[LocationContractReference, LocationContractReference], tuple[str, ...]
    ],
) -> Mapping[tuple[str, str, str], AcceptedSource]:
    """Capture routes by caller, binding path, and receiving input.

    Nested bindings reference the declaring caller's inputs. Explicit operations
    supply their fixed target provider, then route to the receiving requirement.

    Returns:
        Immutable routes, including direct zero-operation bindings.

    Raises:
        ValueError: If a receiving input cannot be supplied by a canonical route.
    """
    result = {}
    for name, owner in graph.nodes.items():
        edges = [
            *graph.dependencies[name].values(),
            *graph.location_dependencies[name].values(),
        ]
        for edge in edges:
            if isinstance(edge, ResolvedDependency):
                producer = edge.output.producer.node
                path = f"inputs.{edge.input_name}"
            else:
                producer = edge.producer.node
                path = edge.binding_path
            for key, receiving in producer.location_inputs.items():
                if receiving.dependency is not None and receiving.dependency.operation:
                    continue
                supply = edge.dependency.locations.get(key, Input(key))
                if isinstance(supply, Input):
                    source = owner.node.location_inputs[supply.path].contract.reference
                elif supply.provider is not None:
                    message = "Location bindings require Input or an operation"
                    raise ValueError(message)
                else:
                    operation = graph.nodes[str(supply.operation)].node
                    if operation.kind != "location_operation":
                        message = f"Expected location operation {supply.operation!r}"
                        raise ValueError(message)
                    source = operation.output_contract.reference
                target = receiving.contract.reference
                if (source, target) not in routes:
                    message = (
                        f"Node {name!r}, binding {path!r}, input {key!r}: "
                        f"No canonical route from {source.collection!r} "
                        f"to {target.collection!r}"
                    )
                    raise ValueError(message)
                result[name, path, key] = AcceptedSource(
                    source, target, routes[source, target]
                )
    return MappingProxyType(result)
