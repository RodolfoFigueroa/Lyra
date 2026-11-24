"""Provider registrations remain independent of service access."""

import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import ValidationError

from lyra.contracts.config import PluginsConfig
from lyra.contracts.errors import PluginDefinitionError, ResourceConfigurationError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.contracts.resources import ResourceConfig, SecretReference
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.resources import ResourceScope, collect_resources
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.plugin_cli import render_description, render_manifest
from lyra.sdk.resources import BaseIO
from lyra.server.registry import Catalog
from test_support.table_fixtures import value_frame, value_schema
from tests.plugin_helpers import DEFINITIONS, plugin_config
from tests.provider_helpers import FakeProvider, create_provider


@metric(
    code_revision="1",
    name="plain",
    description="No location required",
    outputs={"plain": value_schema()},
)
def plain() -> pd.DataFrame:
    return value_frame()


def _manifest(definition: PluginDefinition, name: str) -> PluginManifest:
    return definition.manifest(
        plugin=PluginInfo(name=name, version="1.0"),
        factory="tests.provider_helpers:create_provider",
    )


def test_provider_inspection_and_loading_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("Acquired during inspection"))
    monkeypatch.setattr(FakeProvider, "__init__", forbidden)
    monkeypatch.setattr(FakeProvider, "setup", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="places"\nversion="1.0"\n'
        '[project.entry-points."lyra.plugins"]\nplugin="tests.provider_helpers:create_provider"\n'
    )
    manifest = PluginManifest.model_validate_json(render_manifest(tmp_path))
    assert manifest.nodes == []
    expected = create_provider().providers
    assert manifest.providers == expected
    loaded = BoundPlugin(create_provider())
    assert loaded.definition.node_names == ()
    assert loaded.resources["places"] is FakeProvider
    described = loaded.definition.providers
    described[0].contract.structure.output.columns.clear()
    assert loaded.definition.providers == expected
    payload = json.loads(render_description(tmp_path, json_output=True))
    assert payload["nodes"] == []
    assert payload["providers"] == [
        provider.model_dump(mode="json") for provider in expected
    ]
    assert "EPSG:27700" in render_description(tmp_path)
    forbidden.assert_not_called()


@pytest.mark.parametrize("identifier", [None, "", 12])
def test_invalid_provider_declarations(identifier: object) -> None:
    provider = type("Invalid", (FakeProvider,), {"id": identifier})
    with pytest.raises(PluginDefinitionError, match="Invalid provider registration"):
        LocalRunner.from_definitions(
            {"test": PluginDefinition(resources={"ordinary": provider})}
        )


def test_resource_only_plugin_and_empty_manifest_validation() -> None:
    infrastructure = PluginDefinition(resources={"ordinary": BaseIO})
    assert _manifest(infrastructure, "infrastructure").resources
    with pytest.raises(PluginDefinitionError, match="requires"):
        PluginDefinition()
    manifest = _manifest(create_provider(), "places")
    with pytest.raises(ValidationError, match="at least one"):
        PluginManifest.model_validate(
            manifest.model_dump() | {"providers": (), "resources": {}}
        )
    with pytest.raises(ValidationError, match="registered resource"):
        PluginManifest.model_validate(manifest.model_dump() | {"resources": {}})


def test_provider_description_consistency() -> None:
    payload = _manifest(create_provider(), "places").model_dump(mode="json")
    payload["providers"][0]["contract"]["provider_scope"]["provider_id"] = ""
    with pytest.raises(ValidationError, match="provider_id"):
        PluginManifest.model_validate_json(json.dumps(payload))


def _catalog(tmp_path: Path, definitions: dict[str, PluginDefinition]) -> Catalog:
    installed = []
    for name, definition in definitions.items():
        DEFINITIONS[name] = definition
        directory = tmp_path / name
        directory.mkdir()
        (directory / "lyra.plugin.json").write_text(
            _manifest(definition, name).model_dump_json()
        )
        installed.append(plugin_config(directory))
    return Catalog.load(PluginsConfig(installed=installed))


def test_provider_only_with_metric_plugin_and_mixed_plugin(tmp_path: Path) -> None:
    definitions = {
        "places": create_provider(),
        "metrics": PluginDefinition(metrics=[plain]),
    }
    catalog = _catalog(tmp_path, definitions)
    runner = LocalRunner.from_definitions(definitions)
    assert catalog.get_loaded_metric_names() == ["plain"]
    assert catalog.compiled.providers == runner.providers
    assert runner.providers[FakeProvider.id].resource == "places"
    assert runner.run("plain", inputs={}).tables["plain"].empty
    mixed = PluginDefinition(metrics=[plain], resources={"places": FakeProvider})
    assert _manifest(mixed, "mixed").providers == create_provider().providers
    assert mixed.node_names == ("plain",)


def test_no_provider_needed_for_location_free_metric() -> None:
    runner = LocalRunner.from_definitions(
        {"metrics": PluginDefinition(metrics=[plain])}
    )
    assert not runner.providers
    assert runner.run("plain", inputs={}).tables["plain"].empty


def test_distinct_providers_and_repeated_shared_registration(tmp_path: Path) -> None:
    class Other(FakeProvider):
        id = "other_records"

    definitions = {
        "first": create_provider(),
        "shared": create_provider(),
        "second": PluginDefinition(resources={"other": Other}),
    }
    runner = LocalRunner.from_definitions(definitions)
    assert set(runner.providers) == {FakeProvider.id, Other.id}
    for catalog in (runner.catalog, _catalog(tmp_path, definitions).compiled):
        assert set(catalog.provider_registry) == {FakeProvider.id, Other.id}
        assert {
            owner: [p.id for p in manifest.providers]
            for owner, manifest in catalog.manifests.items()
        } == {
            "first": [FakeProvider.id],
            "shared": [FakeProvider.id],
            "second": [Other.id],
        }


def test_competing_provider_registrations_rejected(tmp_path: Path) -> None:
    definitions = {
        "first": create_provider(),
        "second": PluginDefinition(resources={"alias": FakeProvider}),
    }
    with pytest.raises(PluginDefinitionError, match="Conflicting provider ID"):
        LocalRunner.from_definitions(definitions)
    with pytest.raises(
        (PluginDefinitionError, ValueError), match="Conflicting provider ID"
    ):
        _catalog(tmp_path, definitions)


def test_selected_resource_reuses_settings_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_REGISTRATION_TOKEN", "fake-token")
    definitions = {"places": create_provider()}
    settings = {
        "places": ResourceConfig(
            config={"label": "configured"},
            secrets={"token": SecretReference(env="PROVIDER_REGISTRATION_TOKEN")},
        )
    }
    runner = LocalRunner.from_definitions(definitions, resource_config=settings)
    resources = collect_resources(
        {name: plugin.resources for name, plugin in definitions.items()}
    )
    scope = ResourceScope(resources, settings)
    with scope.activate():
        provider = scope.get(runner.providers[FakeProvider.id].resource)
        assert isinstance(provider, FakeProvider)
        assert provider.config.label == "configured"
        assert dict(provider.secrets) == {"token": "fake-token"}


def test_conflicting_named_resources_rejected(tmp_path: Path) -> None:
    definitions = {
        "places": create_provider(),
        "metrics": PluginDefinition(metrics=[plain], resources={"places": BaseIO}),
    }
    with pytest.raises(ResourceConfigurationError, match="Conflicting resource"):
        LocalRunner.from_definitions(definitions)
    with pytest.raises(ResourceConfigurationError, match="Conflicting resource"):
        _catalog(tmp_path, definitions)


def test_abstract_provider_is_not_registrable() -> None:
    abstract = type(
        "AbstractProvider",
        (LocationProvider,),
        {"id": "abstract", "schema": FakeProvider.schema},
    )
    with pytest.raises(PluginDefinitionError, match="must be concrete"):
        LocalRunner.from_definitions(
            {"test": PluginDefinition(resources={"places": abstract})}
        )


def test_disabled_provider_does_not_compete(tmp_path: Path) -> None:
    installed = []
    for name in ("active", "disabled"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "lyra.plugin.json").write_text(
            _manifest(create_provider(), name).model_dump_json()
        )
        entry = plugin_config(directory)
        entry.enabled = name == "active"
        installed.append(entry)
    catalog = Catalog.load(PluginsConfig(installed=installed))
    assert catalog.compiled.providers[FakeProvider.id].distribution == "active"
    assert catalog.get_loaded_metric_names() == []


def test_duplicate_manifest_provider_ids_are_rejected() -> None:
    payload = _manifest(create_provider(), "places").model_dump(mode="json")
    payload["providers"] *= 2
    with pytest.raises(ValidationError, match="Provider IDs must be unique"):
        PluginManifest.model_validate_json(json.dumps(payload))
