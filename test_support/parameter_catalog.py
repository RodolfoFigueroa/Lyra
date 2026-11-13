"""Live model bindings for metadata-only acceptance fixtures."""

from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import BaseModel

from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.sdk.plugin import NodeImplementation, PluginDefinition


def with_models(
    catalog: CompiledCatalog, models: Mapping[str, type[BaseModel]]
) -> CompiledCatalog:
    """Return a catalog whose fixture nodes validate through explicit live models."""
    implementations = {}
    for distribution, manifest in catalog.manifests.items():
        definition = Mock(spec=PluginDefinition)
        definition.declarations = tuple(
            SimpleNamespace(name=node.name, inputs=dict(node.inputs))
            for node in manifest.nodes
        )
        definition.resources = {}
        definition.node_names = tuple(node.name for node in manifest.nodes)
        nodes = {
            node.name: NodeImplementation(
                function=Mock(side_effect=AssertionError("Unexpected handler")),
                parameters=models.get(node.name),
                accepts_context=False,
                manifest=node,
                schemas={key: Mock() for key in node.outputs}
                if node.kind == "metric"
                else {},
            )
            for node in manifest.nodes
        }
        definition.implementation.side_effect = nodes.__getitem__
        implementations[distribution] = BoundPlugin(definition)
    return replace(catalog, implementations=implementations)
