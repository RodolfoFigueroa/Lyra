"""Resolve declaration uses and identities before acquiring provider records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from lyra.contracts.dependencies import Input
from lyra.contracts.value_identity import ComputationIdentity, value_key
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

    binding_path: str | None = None
    computations: tuple[tuple[str, str | None], ...] = ()
    pairs: tuple[tuple[str, str], ...] = ()
    source: BoundLocation | None = None
    lineage: object = field(default_factory=object, compare=False)

    def select(self, ids: frozenset[str]) -> BoundLocation:
        """Return target owners with every incoming source pair."""
        if ids == frozenset(self.ids or ()):
            return self
        pairs = tuple(pair for pair in self.pairs if pair[1] in ids)
        sources = {source for source, _ in pairs}
        return replace(
            self,
            ids=tuple(sorted(ids)),
            pairs=pairs,
            computations=tuple(
                (key, group)
                for key, group in self.computations
                if group is None or group in sources
            )
            if ids
            else self.computations,
        )

    def related(self, primary: BoundLocation) -> BoundLocation | None:
        """Return related groups by following declared correspondence edges."""
        ancestors = {}
        current = primary
        ids = frozenset(current.ids or ())
        while True:
            ancestors[current.lineage] = ids
            if current.source is None:
                break
            ids = frozenset(a for a, b in current.pairs if b in ids)
            current = current.source
        chain = []
        current = self
        while current.lineage not in ancestors:
            if current.source is None:
                return None
            chain.append(current)
            current = current.source
        selected = current.select(ancestors[current.lineage])
        for hop in reversed(chain):
            pairs = tuple((a, b) for a, b in hop.pairs if a in (selected.ids or ()))
            selected = replace(
                hop.select(frozenset(b for _, b in pairs)),
                pairs=pairs,
                computations=tuple(
                    (key, group)
                    for key, group in hop.computations
                    if group is None or group in (selected.ids or ())
                ),
            )
        return selected

    @property
    def identity(self) -> str:
        """Canonical unordered selection, or the operation discovering target IDs."""
        return value_key(
            {
                "provider": self.provider.provider_scope.identity_payload
                if self.provider.provider_scope
                else self.provider.identity_payload,
                "ids": sorted(set(self.ids)) if self.ids is not None else None,
                "upstream": [
                    {"computation": key, "group": group}
                    for key, group in sorted(
                        self.computations, key=lambda x: (x[0], x[1] or "")
                    )
                ]
                if self.computations
                else self.upstream.identity
                if self.upstream
                else None,
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
    descriptor: ComputationIdentity
    per_location: bool
    outputs: Mapping[str, str]

    def parameter_values(self) -> dict:
        """Return detached complete values for a handler or inspection."""
        return json.loads(self.effective_json)

    def demand(self, groups: frozenset[str] | None = None) -> BoundDemand:
        """Return requested work separate from this use's original binding."""
        return BoundDemand(self, groups)


@dataclass(frozen=True)
class BoundDemand:
    """Acquisition-free demand over an unchanged, complete producer binding.

    Unknown operation-derived membership stays symbolic until correspondences
    are available. It must never be treated as an empty selection.
    """

    invocation: BoundInvocation
    groups: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """Reject requests outside known original membership.

        Raises:
            ValueError: If requested groups exceed the bound selection.
        """
        bound = self.invocation
        primary = bound.locations.get(bound.primary_input or "")
        if (
            self.groups is not None
            and primary is not None
            and primary.ids is not None
            and not self.groups.issubset(primary.ids)
        ):
            message = "Primary demand exceeds the original bound selection"
            raise ValueError(message)

    def computation_key(self, groups: frozenset[str] | None = None) -> str:
        """Return a canonical key using compact references to named inputs."""
        bound = self.invocation
        primary = bound.locations.get(bound.primary_input or "")
        selected_primary = (
            primary.select(groups)
            if primary is not None and groups is not None
            else primary
        )
        inputs: dict = {}
        for name, value in bound.locations.items():
            selected = (
                value.related(selected_primary)
                if selected_primary is not None
                else None
            )
            inputs[name] = (selected or value).identity
        for name, dependency in bound.dependencies.items():
            producer_primary = dependency.locations.get(dependency.primary_input or "")
            selected = (
                producer_primary.related(selected_primary)
                if producer_primary is not None and selected_primary is not None
                else None
            )
            inputs[name] = dependency.demand(
                frozenset(selected.ids or ()) if selected is not None else None
            ).reference(bound.outputs[name])
        return replace(
            bound.descriptor,
            inputs=inputs,
            computation_scope="per_location" if bound.per_location else "complete",
        ).key

    @property
    def computation_keys(self) -> dict[str | None, str] | None:
        """Applicable keys, or None while partition membership is unknown."""
        bound = self.invocation
        if not bound.per_location:
            return {None: self.computation_key()}
        primary = bound.locations[bound.primary_input or ""]
        if primary.ids is None:
            return None
        groups = self.groups if self.groups is not None else frozenset(primary.ids)
        return {
            group: self.computation_key(frozenset({group})) for group in sorted(groups)
        }

    def reference(self, output: str) -> dict:
        """Return complete producer or independent partition references."""
        keys = self.computation_keys
        if keys is None:
            return {"computation": self.invocation.identity, "output": output}
        if not self.invocation.per_location:
            result = {"computation": keys[None], "output": output}
            if self.groups is not None and self.invocation.primary_input is not None:
                result["groups"] = sorted(self.groups)
            return result
        result = {"output": output, "partitions": keys}
        if not keys:
            result["empty_context"] = self.computation_key(frozenset())
        return result

    @property
    def identity(self) -> str:
        """Use the same logical computations for execution-local memoization."""
        keys = self.computation_keys
        if keys is None:
            return self.invocation.descriptor.key
        if not self.invocation.per_location:
            return keys[None]
        return value_key(
            {"partitions": keys}
            if keys
            else {"empty": self.computation_key(frozenset())}
        )


def bind_execution(
    catalog: CompiledCatalog, prepared: PreparedExecution
) -> BoundInvocation:
    """Resolve the complete selected plan without providers, resources or handlers.

    Returns:
        The root use, retaining all dependency and operation uses.
    """
    return _Binder(catalog).bind(prepared)


def bind_resolved(
    catalog: CompiledCatalog,
    name: str,
    locations: Mapping[str, BoundLocation],
    parameters: dict[str, dict],
    routes: dict[str, AcceptedSource],
) -> BoundInvocation:
    """Return the same declared identity for already acquired native inputs."""
    return _Binder(catalog, parameters, routes).visit(name, locations, "root")


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
                operation.output_contract.reference, None, invocation, binding_path=path
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
        descriptor = ComputationIdentity(
            kind="metric" if node.kind == "metric" else "operation",
            node_id=name,
            code_revision=node.code_revision,
            output_scope=node.scope if node.kind == "metric" else "location",
            parameters=effective,
            primary_input=node.primary_input,
            computation_scope="per_location"
            if node.cache == "per_location"
            else "complete",
            inputs={key: value.identity for key, value in locations.items()}
            | {
                key: {"computation": value.identity, "output": node.inputs[key].output}
                for key, value in dependencies.items()
            },
            origins=frozenset(node.origins) if node.kind == "metric" else frozenset(),
            target=node.output_contract.provider_scope
            if node.kind == "location_operation"
            else None,
        )
        invocation = BoundInvocation(
            name,
            scope,
            json.dumps(effective, sort_keys=True, allow_nan=False),
            MappingProxyType(locations),
            MappingProxyType(dependencies),
            MappingProxyType(bindings),
            _origins(node, locations, dependencies),
            node.primary_input,
            descriptor.key,
            descriptor,
            node.cache == "per_location",
            MappingProxyType({key: node.inputs[key].output for key in dependencies}),
        )
        return replace(invocation, identity=invocation.demand().identity)

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
                    operation.output_contract.reference,
                    None,
                    invocation,
                    binding_path=nested_path,
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
