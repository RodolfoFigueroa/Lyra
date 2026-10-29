"""Provider registration and selection remain independent of service access."""

import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    BaseIO,
    LocalRunner,
    LocationProvider,
    PluginDefinition,
    PluginDefinitionError,
    metric,
)
from lyra.sdk.config import PluginsConfig
from lyra.sdk.locations import select_location_provider
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from lyra.sdk.plugin_cli import build_manifest, check_manifest, render_description
from lyra.sdk.plugin_loader import load_validated_plugin_definition
from lyra.sdk.resources import (
    ResourceConfig,
    ResourceConfigurationError,
    ResourceScope,
    SecretReference,
    collect_resources,
)
from pydantic import ValidationError

from lyra_app.registry import Catalog
from tests.plugin_helpers import plugin_config
from tests.test_location_providers import COLLECTION, FakeProvider


@metric(
    name="plain",
    description="No location required",
    outputs={"plain": pa.DataFrameSchema()},
)
def plain() -> pd.DataFrame:
    return pd.DataFrame()


def create_provider() -> PluginDefinition:
    return PluginDefinition(
        resources={"places": FakeProvider}, location_provider="places"
    )


def _manifest(definition: PluginDefinition, name: str) -> PluginManifest:
    return definition.manifest(
        plugin=PluginInfo(name=name, version="1.0"),
        factory="tests.test_provider_registration:create_provider",
    )


def test_provider_inspection_and_loading_without_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("Acquired during inspection"))
    monkeypatch.setattr(FakeProvider, "__init__", forbidden)
    monkeypatch.setattr(FakeProvider, "setup", forbidden)
    monkeypatch.setattr(SecretReference, "resolve", forbidden)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="places"\nversion="1.0"\n'
        '[tool.lyra]\nfactory="tests.test_provider_registration:create_provider"\n'
    )
    path = build_manifest(tmp_path)
    assert check_manifest(tmp_path) == (True, "")
    manifest = PluginManifest.model_validate_json(path.read_text())
    assert manifest.nodes == []
    assert manifest.location_provider is not None
    assert manifest.location_provider.collections == (COLLECTION,)
    loaded = load_validated_plugin_definition(manifest)
    assert loaded.node_names == ()
    payload = json.loads(render_description(tmp_path, json_output=True))
    assert payload["nodes"] == []
    assert payload["location_provider"] == {
        "resource": "places",
        "collections": [COLLECTION.model_dump(mode="json")],
    }
    assert "EPSG:27700" in render_description(tmp_path)
    forbidden.assert_not_called()


@pytest.mark.parametrize("resource", ["missing", "ordinary", ""])
def test_invalid_provider_references(resource: str) -> None:
    with pytest.raises(PluginDefinitionError, match="location provider resource"):
        PluginDefinition(resources={"ordinary": BaseIO}, location_provider=resource)


def test_empty_plugin_and_manifest_are_invalid() -> None:
    with pytest.raises(PluginDefinitionError, match="requires"):
        PluginDefinition(resources={"places": FakeProvider})
    manifest = _manifest(create_provider(), "places")
    with pytest.raises(ValidationError, match="at least one"):
        PluginManifest.model_validate(
            manifest.model_dump() | {"location_provider": None}
        )
    with pytest.raises(ValidationError, match="registered resource"):
        PluginManifest.model_validate(manifest.model_dump() | {"resources": {}})


@pytest.mark.parametrize("change", ["resource", "crs", "unit_types", "collection"])
def test_provider_manifest_integrity(change: str) -> None:
    payload = _manifest(create_provider(), "places").model_dump(mode="json")
    declaration = payload["location_provider"]
    if change == "resource":
        payload["resources"]["other"] = payload["resources"]["places"]
        declaration["resource"] = "other"
    else:
        key = "name" if change == "collection" else change
        declaration["collections"][0][key] = {
            "crs": "EPSG:4326",
            "unit_types": ["parcel"],
            "name": "other:features",
        }[key]
    manifest = PluginManifest.model_validate_json(json.dumps(payload))
    with pytest.raises(RuntimeError, match="does not match"):
        load_validated_plugin_definition(manifest)


def _catalog(tmp_path: Path, definitions: dict[str, PluginDefinition]) -> Catalog:
    installed = []
    for name, definition in definitions.items():
        directory = tmp_path / name
        directory.mkdir()
        (directory / "lyra.plugin.json").write_text(
            _manifest(definition, name).model_dump_json()
        )
        installed.append(plugin_config(directory))
    return Catalog.load(
        PluginsConfig(
            installed=installed, default_queue="default", allowed_queues=["default"]
        )
    )


def test_provider_only_with_metric_plugin_and_mixed_plugin(tmp_path: Path) -> None:
    definitions = {
        "places": create_provider(),
        "metrics": PluginDefinition(nodes=[plain]),
    }
    catalog = _catalog(tmp_path, definitions)
    runner = LocalRunner.from_definitions(definitions)
    assert catalog.get_loaded_metric_names() == ["plain"]
    assert catalog.location_provider == runner.location_provider
    assert runner.location_provider is not None
    assert runner.location_provider.resource == "places"
    assert runner.run("plain", {})["plain"].empty
    mixed = PluginDefinition(
        nodes=[plain], resources={"places": FakeProvider}, location_provider="places"
    )
    assert (
        _manifest(mixed, "mixed").location_provider
        == create_provider().location_provider
    )
    assert mixed.node_names == ("plain",)


def test_no_provider_is_optional_until_resolution_required() -> None:
    runner = LocalRunner.from_definitions({"metrics": PluginDefinition(nodes=[plain])})
    assert runner.location_provider is None
    assert runner.run("plain", {})["plain"].empty
    with pytest.raises(ResourceConfigurationError, match="requires a configured"):
        select_location_provider({"metrics": None}, required=True)


def test_multiple_providers_rejected_in_local_and_catalog(tmp_path: Path) -> None:
    definitions = {"first": create_provider(), "second": create_provider()}
    with pytest.raises(
        ResourceConfigurationError, match=r"Multiple active.*first, second"
    ):
        LocalRunner.from_definitions(definitions)
    with pytest.raises(
        ResourceConfigurationError, match=r"Multiple active.*first, second"
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
    selected = runner.location_provider
    assert selected is not None
    scope = ResourceScope(collect_resources(definitions), settings)
    with scope.activate():
        provider = scope.get(selected.resource)
        assert isinstance(provider, FakeProvider)
        assert provider.config.label == "configured"
        assert dict(provider.secrets) == {"token": "fake-token"}


def test_conflicting_named_resources_rejected(tmp_path: Path) -> None:
    definitions = {
        "places": create_provider(),
        "metrics": PluginDefinition(nodes=[plain], resources={"places": BaseIO}),
    }
    with pytest.raises(ResourceConfigurationError, match="Conflicting resource"):
        LocalRunner.from_definitions(definitions)
    with pytest.raises(ValueError, match="Conflicting resource"):
        _catalog(tmp_path, definitions)


def test_abstract_provider_is_not_registrable() -> None:
    abstract = type(
        "AbstractProvider", (LocationProvider,), {"collections": (COLLECTION,)}
    )
    with pytest.raises(PluginDefinitionError, match="incompatible location provider"):
        PluginDefinition(resources={"places": abstract}, location_provider="places")


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
    catalog = Catalog.load(
        PluginsConfig(
            installed=installed, default_queue="default", allowed_queues=["default"]
        )
    )
    assert catalog.location_provider is not None
    assert catalog.location_provider.distribution == "active"
    assert catalog.get_loaded_metric_names() == []


def test_duplicate_manifest_collections_are_rejected() -> None:
    payload = _manifest(create_provider(), "places").model_dump(mode="json")
    payload["location_provider"]["collections"] *= 2
    with pytest.raises(ValidationError, match="collection names must be unique"):
        PluginManifest.model_validate_json(json.dumps(payload))
