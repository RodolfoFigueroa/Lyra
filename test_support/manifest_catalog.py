"""Description fixtures for metadata-only planning tests."""

from collections.abc import Mapping
from copy import deepcopy

from lyra.contracts.plugins import PluginManifest
from lyra.runtime.planning.catalog import CompiledCatalog, assemble_catalog
from test_support.manifest_graph import build_graph


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
    return assemble_catalog(graph, captured)
