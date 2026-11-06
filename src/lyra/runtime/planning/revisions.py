"""Immutable, concrete-contract revision closures for execution preparation."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.revisions import RevisionBinding, RevisionPlan
from lyra.runtime.planning.graph import PluginGraph
from lyra.runtime.planning.providers import SelectedLocationProvider


@dataclass(frozen=True)
class CatalogRevisions:
    """Captured data identities independent of structural namespaces."""

    contracts: Mapping[LocationContractReference, RevisionBinding]
    nodes: Mapping[str, frozenset[RevisionBinding]]

    def plan(
        self,
        node: str,
        selections: tuple[tuple[LocationContractReference, tuple[str, ...]], ...] = (),
    ) -> RevisionPlan:
        """Return bindings for exactly the selected sources and operation paths."""
        selection = set()
        for source, operations in selections:
            selection.add(self.contracts[source])
            for operation in operations:
                selection.update(self.nodes[operation])
        return RevisionPlan(self.nodes[node], frozenset(selection))


def compile_revisions(
    graph: PluginGraph, provider: SelectedLocationProvider | None
) -> CatalogRevisions:
    """Capture native, target, and transitive calculation dataset revisions.

    Returns:
        Immutable bindings detached from mutable declarations.

    Raises:
        ValueError: If one concrete dataset has conflicting revisions.
    """
    contracts = {}
    declarations = [
        contract
        for entry in graph.nodes.values()
        for contract in (
            *(value.contract for value in entry.node.location_inputs.values()),
            *(
                ()
                if entry.node.kind != "location_operation"
                else (entry.node.output_contract,)
            ),
        )
    ]
    if provider is not None:
        declarations.extend(provider.declaration.contracts)
    for contract in declarations:
        binding = RevisionBinding(contract.reference, contract.data_revision)
        if binding.contract in contracts and contracts[binding.contract] != binding:
            message = f"Conflicting location revisions: {binding.contract!r}"
            raise ValueError(message)
        contracts[binding.contract] = binding
    nodes: dict[str, frozenset[RevisionBinding]] = {}
    for name in graph.order:
        node = graph.nodes[name].node
        relevant = {
            contracts[c.reference]
            for c in (value.contract for value in node.location_inputs.values())
        }
        if node.kind == "location_operation":
            relevant.add(contracts[node.output_contract.reference])
        for edge in graph.dependencies[name].values():
            relevant.update(nodes[edge.output.producer.node.name])
        for edge in graph.location_dependencies[name].values():
            relevant.update(nodes[edge.producer.node.name])
        nodes[name] = frozenset(relevant)
    return CatalogRevisions(MappingProxyType(contracts), MappingProxyType(nodes))
