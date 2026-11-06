"""Compiled computation identities, independent of editorial metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter

from lyra.contracts.locations import LocationContract, LocationContractReference
from lyra.contracts.outputs import CheckMetadata, ColumnMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)
from lyra.contracts.revisions import CacheRevision
from lyra.contracts.schema import schema_nodes
from lyra.runtime.planning.manifests import installed_version
from lyra.runtime.planning.revisions import CatalogRevisions, compile_revisions

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.contracts.accepted import PreparedInput
    from lyra.runtime.planning.graph import PluginGraph
    from lyra.runtime.planning.providers import SelectedLocationProvider


def validate_cache_revision(value: str | None) -> str | None:
    """Return a validated caller revision without trimming or coercion."""
    return TypeAdapter(CacheRevision | None).validate_python(value)


PROTOCOLS = {"computation": 2, "invocation": 1, "content": 1, "artifacts": 2}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    """Return the digest of a deterministic JSON representation."""
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _contract_exclusions(value: object) -> dict:
    if isinstance(value, BaseModel):
        result: dict = {
            name: _contract_exclusions(getattr(value, name))
            for name in type(value).model_fields
        }
        if isinstance(
            value,
            (
                MetricManifest,
                LocationOperationManifest,
                LocationInputDeclaration,
                ColumnMetadata,
                CheckMetadata,
            ),
        ):
            result["description"] = True
        if isinstance(value, MetricManifest):
            result.update(private=True, edition_metadata=True)
        if isinstance(value, LocationOperationManifest):
            result["canonical"] = True
        if isinstance(value, LocationInputDeclaration):
            result["source_bindings"] = True
        if isinstance(value, LocationContract):
            result["data_revision"] = True
        return result
    if isinstance(value, dict):
        return {key: _contract_exclusions(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return {index: _contract_exclusions(item) for index, item in enumerate(value)}
    return {}


def executable_contract(model: BaseModel) -> object:
    """Project declared semantics without interpreting literal user data.

    Dataset revisions have their own pinned execution and cache identities.

    Returns:
        Detached executable and semantic metadata.
    """
    result = model.model_dump(mode="json", exclude=_contract_exclusions(model))
    if isinstance(model, (MetricManifest, LocationOperationManifest)):
        schema = result.get("parameters_schema")
        if schema is not None:
            for _, node in schema_nodes(schema):
                for key in ("title", "description", "examples", "$comment"):
                    node.pop(key, None)
    return result


@dataclass(frozen=True)
class CatalogComputation:
    """Owned immutable identity fragments captured once per compiled catalog."""

    nodes: Mapping[str, str]
    contracts: Mapping[str, str]
    versions: Mapping[str, str | None]
    sources: Mapping[LocationContractReference, str]
    cache_nodes: Mapping[str, str]

    def plan(self, node: str, inputs: tuple[PreparedInput, ...]) -> str:
        """Return identity for captured selections without consulting routing policy."""
        return digest(
            {
                "root": self.nodes[node],
                "inputs": {
                    item.name: {
                        "source": self.sources[item.binding.source],
                        "target": item.binding.target.model_dump(mode="json"),
                        "operations": [
                            (name, self.nodes[name]) for name in item.binding.operations
                        ],
                    }
                    for item in inputs
                },
            }
        )


def compile_computation(
    graph: PluginGraph,
    provider: SelectedLocationProvider | None,
    revisions: CatalogRevisions | None = None,
) -> CatalogComputation:
    """Return captured semantic contracts and dependency identities."""
    distributions = {entry.distribution for entry in graph.nodes.values()}
    if provider is not None:
        distributions.add(provider.distribution)
    versions = {name: installed_version(name) for name in sorted(distributions)}
    contracts = {
        name: _json(executable_contract(entry.node))
        for name, entry in graph.nodes.items()
    }
    sources = {
        contract.reference: digest(executable_contract(contract))
        for entry in graph.nodes.values()
        for contract in (
            *(item.contract for item in entry.node.location_inputs.values()),
            *(
                (entry.node.output_contract,)
                if entry.node.kind == "location_operation"
                else ()
            ),
        )
    }
    provider_identity = None
    if provider is not None:
        declaration = provider.declaration
        collections = {item.name: item for item in declaration.collections}
        owner = {
            "distribution": provider.distribution,
            "version": versions[provider.distribution],
            "resource": declaration.resource,
        }
        for contract in declaration.contracts:
            collection = collections[contract.family.collection]
            unit = contract.family.unit_type
            sources[contract.reference] = digest(
                {
                    **owner,
                    "contract": executable_contract(contract),
                    "crs": collection.crs,
                    "output": executable_contract(collection.outputs[unit])
                    if unit in collection.outputs
                    else None,
                }
            )
        # Dynamic operation lookups are not enumerated by node declarations.
        provider_identity = digest(
            {
                **owner,
                "sources": sorted(
                    sources[item.reference] for item in declaration.contracts
                ),
            }
        )
    nodes: dict[str, str] = {}
    for name in graph.order:
        entry = graph.nodes[name]
        dependencies = {
            edge.output.producer.node.name for edge in graph.dependencies[name].values()
        } | {
            edge.producer.node.name
            for edge in graph.location_dependencies[name].values()
        }
        nodes[name] = digest(
            {
                "protocols": PROTOCOLS,
                "contract": contracts[name],
                "distribution": entry.distribution,
                "version": versions[entry.distribution],
                "dependencies": {
                    dependency: nodes[dependency] for dependency in sorted(dependencies)
                },
                "provider": provider_identity
                if entry.node.kind == "location_operation"
                else None,
            }
        )
    captured_revisions = (
        revisions if revisions is not None else compile_revisions(graph, provider)
    )
    cache_nodes = {
        name: digest(
            {
                "computation": nodes[name],
                "revisions": sorted(
                    (_json(binding.contract.model_dump(mode="json")), binding.revision)
                    for binding in bindings
                ),
            }
        )
        for name, bindings in captured_revisions.nodes.items()
    }
    return CatalogComputation(
        MappingProxyType(nodes),
        MappingProxyType(contracts),
        MappingProxyType(versions),
        MappingProxyType(sources),
        MappingProxyType(cache_nodes),
    )
