"""Installed-distribution metadata doubles for plugin integration tests."""

from __future__ import annotations

import inspect
import json
import tomllib
from typing import TYPE_CHECKING, Annotated

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.contracts.config import InstalledPluginConfig, normalize_distribution_name
from lyra.contracts.plugins import MetricManifest, PluginManifest
from lyra.contracts.providers import ProviderDeclaration
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.loading import load_plugin_definition
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_resolution import Provider
from tests.contract_helpers import PARAMETER_MODELS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

MANIFEST_FILENAME = "lyra.plugin.json"

VERSIONS: dict[str, str] = {}


def plugin_config(path: Path) -> InstalledPluginConfig:
    """Register fake installed metadata for a local project or declaration fixture."""
    project = path / "pyproject.toml"
    if project.is_file():
        metadata = tomllib.loads(project.read_text())["project"]
        name = normalize_distribution_name(metadata["name"])
        VERSIONS[name] = metadata["version"]
        PATHS[name] = path
        return InstalledPluginConfig(distribution=name)
    manifest_path = path / "lyra.plugin.json"
    raw = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    info = raw.get("plugin", {"name": "missing-plugin", "version": "1.0.0"})
    name = normalize_distribution_name(info["name"])
    VERSIONS[name] = info["version"]
    PATHS[name] = path
    return InstalledPluginConfig(distribution=name)


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
    handlers = []
    for node in manifest.nodes:
        if not isinstance(node, MetricManifest):
            msg = "The test installation requires a metric declaration"
            raise TypeError(msg)
        handlers.append(_inspection_metric(node))
    resources = {value.resource: Provider for value in manifest.providers}
    if not resources and any(node.location_inputs for node in manifest.nodes):
        resources["locations"] = Provider
    return PluginDefinition(metrics=handlers, resources=resources)


def _inspection_metric(node: MetricManifest) -> Callable:
    """Declare a service-free metric with the fixture's named inputs and outputs."""

    def handler(**_arguments: object) -> pd.DataFrame:
        msg = "Inspection invoked a handler"
        raise AssertionError(msg)

    inputs: dict[str, Dependency | LocationDependency] = dict(node.inputs)
    hints: dict[str, object] = dict.fromkeys(inputs, pd.DataFrame)
    for name, location in node.location_inputs.items():
        inputs[name] = LocationDependency(
            provider=location.contract.provider_scope.provider_id
        )
        hints[name] = (
            Annotated[gpd.GeoDataFrame, Field(description=location.description)]
            if location.description
            else gpd.GeoDataFrame
        )
    if node.parameters_schema is not None:
        hints["parameters"] = PARAMETER_MODELS[
            json.dumps(node.parameters_schema, sort_keys=True)
        ]
    handler.__annotations__ = hints
    vars(handler)["__signature__"] = inspect.Signature(
        [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY) for name in hints]
    )
    outputs = {
        key: pa.DataFrameSchema(
            {
                column.name: pa.Column(
                    column.dtype,
                    nullable=column.nullable,
                    description=column.description,
                    metadata={"unit": column.unit},
                )
                for column in output.columns
            },
            strict=True,
        )
        for key, output in node.outputs.items()
    }
    return metric(
        name=node.name,
        code_revision=node.code_revision,
        description=node.description,
        outputs=outputs,
        inputs=inputs,
        private=node.private,
    )(handler)
