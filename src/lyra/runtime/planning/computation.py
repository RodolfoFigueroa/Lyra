"""Accepted-plan validation hashes, separate from declared value identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import BaseModel

from lyra.contracts.locations import LocationContract
from lyra.contracts.outputs import CheckMetadata, ColumnMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.providers import ProviderDeclaration
from lyra.contracts.schema import schema_nodes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lyra.contracts.accepted import AcceptedSource, PreparedInput
    from lyra.runtime.planning.graph import PluginGraph
    from lyra.runtime.planning.providers import SelectedLocationProvider


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
                ProviderDeclaration,
                CheckMetadata,
            ),
        ):
            result["description"] = True
        if isinstance(value, MetricManifest):
            result.update(private=True)
        if isinstance(value, LocationOperationManifest):
            result["canonical"] = True
        if isinstance(value, LocationInputDeclaration):
            # Inspection includes lifecycle metadata; the contract owns identity.
            result["provider"] = True
        if isinstance(value, LocationContract):
            result["data_revision"] = True
        return result
    if isinstance(value, dict):
        return {key: _contract_exclusions(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return {index: _contract_exclusions(item) for index, item in enumerate(value)}
    return {}


def _normalize_provider_scopes(value: object, serialized: object) -> None:
    if isinstance(value, ProviderScope) and isinstance(serialized, dict):
        serialized.clear()
        serialized.update(value.identity_payload)
    elif isinstance(value, BaseModel) and isinstance(serialized, dict):
        for name, child in serialized.items():
            if name in type(value).model_fields:
                _normalize_provider_scopes(getattr(value, name), child)
    elif isinstance(value, dict) and isinstance(serialized, dict):
        for name, child in serialized.items():
            _normalize_provider_scopes(value[name], child)
    elif isinstance(value, tuple | list) and isinstance(serialized, list):
        for source, child in zip(value, serialized, strict=True):
            _normalize_provider_scopes(source, child)


def executable_contract(model: BaseModel) -> object:
    """Project declared semantics without interpreting literal user data.

    Dataset revisions have their own pinned execution and cache identities.

    Returns:
        Detached executable and semantic metadata.
    """
    result = model.model_dump(mode="json", exclude=_contract_exclusions(model))
    _normalize_provider_scopes(model, result)
    if isinstance(model, MetricManifest):
        # Origin collections are unordered. Descriptions and whichever duplicate
        # representative survived collection must not affect cache identity.
        result["origins"] = sorted(
            (
                {"id": origin.id, "revision": origin.revision}
                for origin in model.origins
            ),
            key=_json,
        )
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
    sources: Mapping[str, str]

    def plan(
        self,
        node: str,
        inputs: tuple[PreparedInput, ...],
        dependency_routes: Mapping[str, AcceptedSource],
        parameter_uses: Mapping[str, dict],
    ) -> str:
        """Return identity for captured selections without consulting routing policy."""
        return digest(
            {
                "root": self.nodes[node],
                "parameters": parameter_uses,
                "dependency_routes": {
                    key: {
                        "source": self.sources[route.source],
                        "target": route.target,
                        "operations": [
                            (name, self.nodes[name]) for name in route.operations
                        ],
                    }
                    for key, route in dependency_routes.items()
                },
                "inputs": {
                    item.name: {
                        "source": self.sources[item.binding.source],
                        "target": item.binding.target,
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
    providers: Mapping[str, SelectedLocationProvider],
) -> CatalogComputation:
    """Return captured semantic contracts and dependency identities."""
    sources = {
        name: digest(provider.declaration.contract.provider_scope.identity_payload)
        for name, provider in providers.items()
    }
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
                "contract": _json(executable_contract(entry.node)),
                "implementation": (entry.node.name, entry.node.code_revision),
                "dependencies": {
                    dependency: nodes[dependency] for dependency in sorted(dependencies)
                },
            }
        )
    return CatalogComputation(
        MappingProxyType(nodes),
        MappingProxyType(sources),
    )
