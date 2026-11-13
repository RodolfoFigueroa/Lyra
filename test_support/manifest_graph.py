"""Resolve enabled manifest graphs without importing calculation code."""

from collections.abc import Mapping, Sequence
from graphlib import CycleError, TopologicalSorter

from lyra.contracts.dependencies import (
    Dependency,
    Input,
    LocationDependency,
)
from lyra.contracts.location_validation import validate_location_families
from lyra.contracts.plugins import NodeManifest
from lyra.contracts.schema import check_parameter_schema
from lyra.runtime.planning.graph import (
    NodeEntry,
    OutputEntry,
    PluginGraph,
    ResolvedDependency,
    ResolvedLocationDependency,
    parameter_contract,
)


def _validate_bindings(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: Dependency | LocationDependency,
    diagnostic: str,
) -> None:
    source = parameter_contract(consumer).get("properties", {})
    target = parameter_contract(producer)
    fields = target.get("properties", {})
    overrides = dependency.parameters
    errors = []
    for name, value in overrides.items():
        if name not in fields:
            errors.append(f"unknown parameter override {name!r}")
        if isinstance(value, Input):
            if not value.path.startswith("parameters."):
                errors.append(
                    f"parameter {name!r} requires a parameter reference, "
                    f"got {value.path!r}"
                )
            elif value.path.split(".")[1] not in source:
                errors.append(f"unknown consumer parameter {value.path!r}")
    errors.extend(
        f"no supply path for required parameter {name!r}"
        for name in target.get("required", [])
        if name not in overrides and name not in source
    )
    errors.extend(_location_binding_errors(consumer, producer, dependency))
    if errors:
        msg = f"{diagnostic}: {'; '.join(errors)}"
        raise ValueError(msg)


def _location_binding_errors(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: Dependency | LocationDependency,
) -> list[str]:
    errors: list[str] = []
    locations = dependency.locations
    for name, value in locations.items():
        if name not in producer.location_inputs:
            errors.append(f"unknown location override {name!r}")
        if isinstance(value, Input):
            if value.path.startswith("parameters."):
                errors.append(
                    f"location {name!r} requires a location reference, "
                    f"got {value.path!r}"
                )
            elif value.path not in consumer.location_inputs:
                errors.append(f"unknown consumer location {value.path!r}")
    errors.extend(
        f"no supply path for required spatial input {name!r}"
        for name, declaration in producer.location_inputs.items()
        if (
            declaration.dependency is None
            or declaration.dependency.provider is not None
        )
        and name not in locations
        and name not in consumer.location_inputs
    )
    errors.extend(_contract_errors(consumer, producer, dependency))
    return errors


def _contract_errors(
    consumer: NodeManifest,
    producer: NodeManifest,
    dependency: Dependency | LocationDependency,
) -> list[str]:
    errors: list[str] = []
    locations = dependency.locations
    for name, declaration in producer.location_inputs.items():
        if (
            declaration.dependency is not None
            and declaration.dependency.operation is not None
        ):
            continue
        binding = locations.get(name)
        source_name = binding.path if isinstance(binding, Input) else name
        source = consumer.location_inputs.get(source_name)
        if (binding is None or isinstance(binding, Input)) and (
            source is None or source.contract != declaration.contract
        ):
            errors.append(
                f"incompatible location contract for receiving input {name!r}"
            )
    return errors


def build_graph(contracts: Mapping[str, Sequence[NodeManifest]]) -> PluginGraph:
    """Validate and resolve all supplied node contracts atomically.

    Returns:
        The complete graph; no implementation modules are imported.

    Raises:
        ValueError: If names, dependency supply paths, or cycles are invalid.
    """
    nodes: dict[str, NodeEntry] = {}
    outputs: dict[tuple[str, str], OutputEntry] = {}
    for distribution, declarations in sorted(contracts.items()):
        for node in declarations:
            if node.name in nodes:
                msg = (
                    f"Duplicate node {node.name!r} in {distribution!r} and "
                    f"{nodes[node.name].distribution!r}"
                )
                raise ValueError(msg)
            entry = NodeEntry(node, distribution)
            nodes[node.name] = entry
            if node.parameters_schema is not None:
                check_parameter_schema(node.parameters_schema)
            for name, declaration in (
                node.outputs.items() if node.kind == "metric" else ()
            ):
                outputs[node.name, name] = OutputEntry(entry, declaration)
    graph = _resolve_graph(nodes, outputs)
    validate_location_families([entry.node for entry in graph.nodes.values()])
    return graph


def _resolve_locations(
    owner: NodeEntry,
    dependency: Dependency | LocationDependency,
    path: str,
    nodes: dict[str, NodeEntry],
    edges: dict[str, ResolvedLocationDependency],
) -> None:
    if isinstance(dependency, LocationDependency) and dependency.operation is None:
        msg = "Provider requirements need the native node compiler"
        raise ValueError(msg)
    receiver = nodes[
        dependency.producer
        if isinstance(dependency, Dependency)
        else str(dependency.operation)
    ].node
    for receiving, binding in dependency.locations.items():
        if isinstance(binding, Input):
            continue
        binding_path = f"{path}.locations.{receiving}"
        context = (
            f"Node {owner.node.name!r} ({owner.node.kind}), binding {binding_path!r}, "
            f"operation {binding.operation!r}"
        )
        if binding.operation is None:
            msg = "Provider requirements need the native node compiler"
            raise ValueError(msg)
        producer = nodes.get(binding.operation)
        if producer is None:
            reason = (
                "expected a location operation, got a table output"
                if any(
                    binding.operation in entry.node.outputs
                    for entry in nodes.values()
                    if entry.node.kind == "metric"
                )
                else "no enabled producer"
            )
            msg = f"{context}: {reason}"
            raise ValueError(msg)
        if producer.node.kind != "location_operation":
            msg = f"{context}: expected a location operation, got a metric"
            raise ValueError(msg)
        receiving_input = receiver.location_inputs.get(receiving)
        if (
            receiving_input is None
            or producer.node.output_contract != receiving_input.contract
        ):
            msg = (
                f"{context}: incompatible location contract "
                f"for receiving input {receiving!r}"
            )
            raise ValueError(msg)
        edge = ResolvedLocationDependency(owner, binding_path, binding, producer)
        _validate_bindings(owner.node, producer.node, binding, edge.diagnostic)
        edges[binding_path] = edge
        _resolve_locations(owner, binding, binding_path, nodes, edges)


def _resolve_graph(
    nodes: dict[str, NodeEntry], outputs: dict[tuple[str, str], OutputEntry]
) -> PluginGraph:
    dependencies: dict[str, dict[str, ResolvedDependency]] = {}
    locations: dict[str, dict[str, ResolvedLocationDependency]] = {}
    predecessors: dict[str, set[str]] = {}
    for name, entry in nodes.items():
        dependencies[name] = {}
        locations[name] = {}
        for input_name, dependency in entry.node.inputs.items():
            if (dependency.producer, dependency.output) not in outputs:
                reason = (
                    "expected a table output, got a location operation"
                    if dependency.producer in nodes
                    and nodes[dependency.producer].node.kind == "location_operation"
                    else f"no enabled producer/output {dependency.producer!r}/"
                    f"{dependency.output!r}"
                )
                msg = (
                    f"Node {name!r} ({entry.node.kind}), input {input_name!r}, "
                    f"output {dependency.output!r}: {reason}"
                )
                raise ValueError(msg)
            edge = ResolvedDependency(
                entry,
                input_name,
                dependency,
                outputs[dependency.producer, dependency.output],
            )
            _validate_bindings(
                entry.node, edge.output.producer.node, dependency, edge.diagnostic
            )
            dependencies[name][input_name] = edge
            _resolve_locations(
                entry,
                dependency,
                f"inputs.{input_name}",
                nodes,
                locations[name],
            )
        for input_name, declaration in entry.node.location_inputs.items():
            dependency = declaration.dependency
            if dependency is None or dependency.operation is None:
                continue
            producer = nodes.get(dependency.operation)
            if producer is None or producer.node.kind != "location_operation":
                message = f"Unknown location operation {dependency.operation!r}"
                raise ValueError(message)
            binding_path = f"inputs.{input_name}"
            edge = ResolvedLocationDependency(entry, binding_path, dependency, producer)
            _validate_bindings(entry.node, producer.node, dependency, edge.diagnostic)
            locations[name][binding_path] = edge
            _resolve_locations(entry, dependency, binding_path, nodes, locations[name])
        predecessors[name] = {
            edge.output.producer.node.name for edge in dependencies[name].values()
        } | {edge.producer.node.name for edge in locations[name].values()}
    try:
        order = tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        details = []
        for consumer, producer in zip(cycle[1:], cycle[:-1], strict=True):
            details.extend(
                edge.diagnostic
                for edge in dependencies[consumer].values()
                if edge.output.producer.node.name == producer
            )
            details.extend(
                edge.diagnostic
                for edge in locations[consumer].values()
                if edge.producer.node.name == producer
            )
        msg = "Dependency cycle: " + "; ".join(details)
        raise ValueError(msg) from exc
    return PluginGraph(nodes, outputs, dependencies, locations, order)
