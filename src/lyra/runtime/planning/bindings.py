"""Resolve declaration uses and identities before acquiring provider records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from lyra.contracts.dependencies import Input
from lyra.runtime.planning.computation import digest, executable_contract
from lyra.runtime.planning.graph import ResolvedDependency
from lyra.runtime.planning.parameters import (
    prepare_parameter_uses,
    receiving_key,
    route_key,
    use_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.accepted import AcceptedSource, PreparedExecution
    from lyra.contracts.locations import LocationContractReference
    from lyra.contracts.origins import Origin
    from lyra.contracts.plugins import NodeManifest
    from lyra.runtime.planning.catalog import CompiledCatalog
    from lyra.runtime.planning.graph import (
        ResolvedLocationDependency,
    )


@dataclass(frozen=True)
class BoundLocation:
    """Provider identity and selection lineage, independent of acquired records."""

    provider: LocationContractReference
    ids: tuple[str, ...] | None
    upstream: BoundInvocation | None = None

    @property
    def identity(self) -> str:
        """Canonical unordered selection, or the operation discovering target IDs."""
        return digest(
            {
                "provider": self.provider.identity_payload,
                "ids": sorted(set(self.ids)) if self.ids is not None else None,
                "upstream": self.upstream.identity if self.upstream else None,
            }
        )


@dataclass(frozen=True)
class BoundInvocation:
    """One owned computation use with immutable effective values and relationships."""

    node: str
    parameter_use_key: str
    effective_json: str
    locations: Mapping[str, BoundLocation]
    dependencies: Mapping[str, BoundInvocation]
    bindings: Mapping[str, BoundInvocation]
    origins: frozenset[Origin]
    primary_input: str | None
    identity: str

    def parameter_values(self) -> dict:
        """Return detached complete values for a handler or inspection."""
        return json.loads(self.effective_json)


def bind_execution(
    catalog: CompiledCatalog, prepared: PreparedExecution
) -> BoundInvocation:
    """Resolve the complete selected plan without providers, resources or handlers.

    Returns:
        The root use, retaining all dependency and operation uses.
    """
    return _Binder(catalog).bind(prepared)


@dataclass
class _Binder:
    catalog: CompiledCatalog
    uses: dict[str, dict] = field(default_factory=dict)
    routes: dict[str, AcceptedSource] = field(default_factory=dict)

    def routed(
        self, value: BoundLocation, target: LocationContractReference, key: str
    ) -> BoundLocation:
        route = self.routes[key]
        if (route.source, route.target) != (value.provider, target):
            message = f"Receiving route differs from bound providers: {key}"
            raise ValueError(message)
        for index, operation in enumerate(route.operations):
            value = self.operation_result(operation, value, route_key(key, index))
        return value

    def operation_result(
        self, name: str, source: BoundLocation, key: str
    ) -> BoundLocation:
        node = self.catalog.graph.nodes[name].node
        if node.kind != "location_operation":
            message = f"Expected operation {name!r}"
            raise ValueError(message)
        source_name = next(iter(node.location_inputs))
        invocation = self.visit(name, {source_name: source}, key)
        return BoundLocation(node.output_contract.reference, None, invocation)

    def visit(
        self, name: str, supplied: Mapping[str, BoundLocation], scope: str
    ) -> BoundInvocation:
        node = self.catalog.graph.nodes[name].node
        effective = self.uses[scope]
        locations = dict(supplied)
        active: set[str] = set()
        bindings: dict[str, BoundInvocation] = {}

        def resolve(key: str) -> BoundLocation:
            if key in locations:
                return locations[key]
            if key in active or key not in node.location_inputs:
                message = f"Missing or cyclic location binding {name!r}.{key}"
                raise ValueError(message)
            active.add(key)
            path = f"inputs.{key}"
            edge = self.catalog.graph.location_dependencies[name].get(path)
            if edge is None:
                message = f"Missing location binding {name!r}.{key}"
                raise ValueError(message)
            invocation = self.edge_use(
                (name, effective, scope), resolve, bindings, edge, path
            )
            operation = edge.producer.node
            if operation.kind != "location_operation":
                message = "Location binding requires an operation"
                raise ValueError(message)
            locations[key] = BoundLocation(
                operation.output_contract.reference, None, invocation
            )
            active.remove(key)
            return locations[key]

        for key in node.location_inputs:
            resolve(key)
        dependencies = {
            key: self.edge_use(
                (name, effective, scope), resolve, bindings, edge, f"inputs.{key}"
            )
            for key, edge in self.catalog.graph.dependencies[name].items()
        }
        payload = {
            "node": name,
            "code_revision": node.code_revision,
            "kind": node.kind,
            "parameters": effective,
            "primary": node.primary_input,
            "locations": {key: value.identity for key, value in locations.items()},
            "dependencies": {
                key: [value.identity, node.inputs[key].output]
                for key, value in dependencies.items()
            },
            "contract": {
                key: executable_contract(value) for key, value in node.outputs.items()
            }
            if node.kind == "metric"
            else node.output_contract.reference.identity_payload,
            "origins": sorted(
                {(origin.id, origin.revision) for origin in node.origins},
                key=json.dumps,
            )
            if node.kind == "metric"
            else [],
        }
        return BoundInvocation(
            name,
            scope,
            json.dumps(effective, sort_keys=True, allow_nan=False),
            MappingProxyType(locations),
            MappingProxyType(dependencies),
            MappingProxyType(bindings),
            _origins(node, locations, dependencies),
            node.primary_input,
            digest(payload),
        )

    def edge_use(
        self,
        owner: tuple[str, dict, str],
        resolve: Callable[[str], BoundLocation],
        bindings: dict[str, BoundInvocation],
        edge: ResolvedDependency | ResolvedLocationDependency,
        path: str,
    ) -> BoundInvocation:
        name, effective, scope = owner
        producer = (
            edge.output.producer
            if isinstance(edge, ResolvedDependency)
            else edge.producer
        ).node
        arguments = {}
        for receiving, declaration in producer.location_inputs.items():
            if declaration.dependency and declaration.dependency.operation:
                continue
            binding = edge.dependency.locations.get(receiving, Input(receiving))
            if isinstance(binding, Input):
                value = resolve(binding.path)
            else:
                nested_path = f"{path}.locations.{receiving}"
                nested = self.catalog.graph.location_dependencies[name][nested_path]
                invocation = self.edge_use(
                    owner, resolve, bindings, nested, nested_path
                )
                operation = nested.producer.node
                if operation.kind != "location_operation":
                    message = "Location binding requires an operation"
                    raise ValueError(message)
                value = BoundLocation(
                    operation.output_contract.reference, None, invocation
                )
            arguments[receiving] = self.routed(
                value,
                declaration.contract.reference,
                receiving_key(scope, path, receiving),
            )
        invocation = self.visit(
            producer.name, arguments, use_key(name, effective, path, scope=scope)
        )
        bindings[path] = invocation
        return invocation

    def bind(self, prepared: PreparedExecution) -> BoundInvocation:
        parameters = prepare_parameter_uses(
            self.catalog,
            prepared.node,
            prepared.parameters,
            prepared.inputs,
            normalized_uses=prepared.parameter_uses,
        )
        self.uses, self.routes = parameters.uses, parameters.routes
        if self.routes != prepared.dependency_routes:
            message = "Selected dependency routes differ from accepted plan"
            raise ValueError(message)
        supplied = {}
        for item in prepared.inputs:
            value = BoundLocation(item.binding.source, item.ids)
            for index, operation in enumerate(item.binding.operations):
                value = self.operation_result(
                    operation, value, route_key(item.name, index)
                )
            supplied[item.name] = value
        return self.visit(prepared.node, supplied, "root")


def _origins(
    node: NodeManifest,
    locations: Mapping[str, BoundLocation],
    dependencies: Mapping[str, BoundInvocation],
) -> frozenset[Origin]:
    origins = set(node.origins) if node.kind == "metric" else set()
    for value in locations.values():
        if value.provider.provider_scope is not None:
            origins.update(value.provider.provider_scope.origins)
        if value.upstream is not None:
            origins.update(value.upstream.origins)
    for dependency in dependencies.values():
        origins.update(dependency.origins)
    if node.kind == "location_operation" and node.output_contract.provider_scope:
        origins.update(node.output_contract.provider_scope.origins)
    return frozenset(origins)
