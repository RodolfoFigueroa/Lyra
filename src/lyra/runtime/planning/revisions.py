"""Immutable, concrete-contract revision closures for execution preparation."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lyra.contracts.revisions import RevisionBinding, RevisionPlan
from lyra.runtime.planning.graph import PluginGraph
from lyra.runtime.planning.providers import SelectedLocationProvider


@dataclass(frozen=True)
class CatalogRevisions:
    """Captured data identities independent of structural namespaces."""

    contracts: Mapping[str, RevisionBinding]
    nodes: Mapping[str, frozenset[RevisionBinding]]

    def plan(
        self,
        node: str,
        selections: tuple[tuple[str, tuple[str, ...]], ...] = (),
        dependency_routes: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> RevisionPlan:
        """Return bindings for exactly the selected sources and operation paths."""
        selection = set()
        for source, operations in selections:
            selection.add(self.contracts[source])
            for operation in operations:
                selection.update(self.nodes[operation])
        calculation = set(self.nodes[node])
        for source, operations in dependency_routes:
            calculation.add(self.contracts[source])
            for operation in operations:
                calculation.update(self.nodes[operation])
        return RevisionPlan(frozenset(calculation), frozenset(selection))


def compile_revisions(
    graph: PluginGraph, providers: Mapping[str, SelectedLocationProvider]
) -> CatalogRevisions:
    """Capture native, target, and transitive calculation dataset revisions.

    Returns:
        Immutable bindings detached from mutable declarations.
    """
    contracts = {
        name: RevisionBinding(
            provider.declaration.contract.reference,
            provider.declaration.contract.data_revision,
        )
        for name, provider in providers.items()
    }
    nodes: dict[str, frozenset[RevisionBinding]] = {}
    for name in graph.order:
        node = graph.nodes[name].node
        relevant = {
            contracts[c.provider_scope.provider_id]
            for c in (value.contract for value in node.location_inputs.values())
        }
        if node.kind == "location_operation":
            relevant.add(contracts[node.output_contract.provider_scope.provider_id])
        for edge in graph.dependencies[name].values():
            relevant.update(nodes[edge.output.producer.node.name])
        for edge in graph.location_dependencies[name].values():
            relevant.update(nodes[edge.producer.node.name])
        nodes[name] = frozenset(relevant)
    return CatalogRevisions(MappingProxyType(contracts), MappingProxyType(nodes))
