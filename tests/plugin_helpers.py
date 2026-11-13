"""Installed-distribution metadata doubles for plugin integration tests."""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

from lyra.contracts.config import InstalledPluginConfig, normalize_distribution_name
from lyra.contracts.plugins import PluginManifest
from lyra.contracts.providers import ProviderDeclaration
from lyra.sdk.dependencies import Dependency
from lyra.sdk.loading import load_plugin_definition
from lyra.sdk.plugin import NodeImplementation, PluginDefinition
from tests.contract_helpers import PARAMETER_MODELS

if TYPE_CHECKING:
    from pathlib import Path

MANIFEST_FILENAME = "lyra.plugin.json"

VERSIONS: dict[str, str] = {}


def plugin_config(
    path: Path, *, routing: dict[str, str] | None = None
) -> InstalledPluginConfig:
    """Register fake installed metadata for a local project or declaration fixture."""
    project = path / "pyproject.toml"
    if project.is_file():
        metadata = tomllib.loads(project.read_text())["project"]
        name = normalize_distribution_name(metadata["name"])
        VERSIONS[name] = metadata["version"]
        PATHS[name] = path
        return InstalledPluginConfig(distribution=name, routing=routing or {})
    manifest_path = path / "lyra.plugin.json"
    raw = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    info = raw.get("plugin", {"name": "missing-plugin", "version": "1.0.0"})
    name = normalize_distribution_name(info["name"])
    VERSIONS[name] = info["version"]
    PATHS[name] = path
    return InstalledPluginConfig(distribution=name, routing=routing or {})


PATHS: dict[str, Path] = {}
DEFINITIONS: dict[str, PluginDefinition] = {}


def fixture_definition(name: str) -> PluginDefinition:
    """Load executable fixtures or build a description-only in-process fake."""
    if name in DEFINITIONS:
        return DEFINITIONS[name]
    project = PATHS[name] / "pyproject.toml"
    if project.is_file():
        metadata = tomllib.loads(project.read_text())["project"]
        return load_plugin_definition(
            metadata["entry-points"]["lyra.plugins"]["plugin"]
        )
    raw = json.loads((PATHS[name] / "lyra.plugin.json").read_text())
    if not raw["factory"].startswith(
        (
            "fake_plugin.",
            "tests.fake:",
            "test:create",
            "test:factory",
            "unavailable:",
            "test_support.edition_catalog:",
        )
    ):
        return load_plugin_definition(raw["factory"])
    for node in raw["nodes"]:
        node["inputs"] = {
            key: Dependency(producer=value["producer"], output=value["output"])
            for key, value in node.get("inputs", {}).items()
        }
    raw["providers"] = [
        ProviderDeclaration.model_validate_json(json.dumps(item))
        for item in raw.get("providers", [])
    ]
    manifest = PluginManifest.model_validate(raw)
    definition = Mock(spec=PluginDefinition)
    definition.declarations = tuple(
        SimpleNamespace(name=node.name, inputs=dict(node.inputs))
        for node in manifest.nodes
    )
    definition.node_manifests = manifest.nodes
    definition.node_names = tuple(node.name for node in manifest.nodes)
    definition.metric_names = tuple(
        node.name for node in manifest.nodes if node.kind == "metric"
    )
    definition.location_operation_names = tuple(
        node.name for node in manifest.nodes if node.kind == "location_operation"
    )
    definition.resources = {}
    definition.providers = manifest.providers
    definition.is_bound = True
    definition.route_preferences = manifest.route_preferences
    nodes = {
        node.name: NodeImplementation(
            function=lambda: None,
            parameters=PARAMETER_MODELS.get(
                json.dumps(node.parameters_schema, sort_keys=True)
            ),
            accepts_context=False,
            manifest=node,
            schemas={key: Mock() for key in node.outputs}
            if node.kind == "metric"
            else {},
        )
        for node in manifest.nodes
    }
    definition.implementation.side_effect = nodes.__getitem__
    definition.manifest.return_value = manifest
    return definition
