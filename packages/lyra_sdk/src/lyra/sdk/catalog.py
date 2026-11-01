"""Compile enabled contracts without importing handlers or acquiring resources."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.locations import SelectedLocationProvider, select_location_provider
from lyra.sdk.models.plugin import PluginManifest


@dataclass(frozen=True)
class CompiledCatalog:
    """Captured contracts shared by application and local execution."""

    manifests: Mapping[str, PluginManifest]
    graph: PluginGraph
    editions: EditionRegistry
    location_provider: SelectedLocationProvider | None


def compile_catalog(manifests: Mapping[str, PluginManifest]) -> CompiledCatalog:
    """Capture and validate the complete enabled catalog without acquisition.

    Returns:
        Reusable metadata detached from caller-owned declarations.

    Raises:
        ValueError: If enabled declarations conflict or references are invalid.
    """
    captured = deepcopy(dict(manifests))
    resources = {}
    for manifest in captured.values():
        for name, declaration in manifest.resources.items():
            if name in resources and resources[name] != declaration:
                message = f"Conflicting resource declarations: {name}"
                raise ValueError(message)
            resources[name] = declaration
    graph = build_graph({key: value.nodes for key, value in captured.items()})
    selected = select_location_provider(
        {key: value.location_provider for key, value in captured.items()}
    )
    return CompiledCatalog(MappingProxyType(captured), graph, graph.editions, selected)
