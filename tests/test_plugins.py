from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from lyra.contracts.config import InstalledPluginConfig, normalize_distribution_name
from lyra.server import plugins, registry
from lyra.server.routes import admin
from tests.config_helpers import load_test_config
from tests.runtime_helpers import make_runtime
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.config import LyraConfig


@pytest.fixture
def config(tmp_path: Path) -> LyraConfig:
    return load_test_config(tmp_path, plugins=[SMOKE_PLUGIN_DIR])


@pytest.mark.parametrize(
    ("name", "expected"),
    [("My_Plugin.Name", "my-plugin-name"), ("a", "a"), ("Foo--Bar", "foo-bar")],
)
def test_distribution_names_are_normalized(name: str, expected: str) -> None:
    assert normalize_distribution_name(name) == expected


@pytest.mark.parametrize(
    "name", ["", "a/b", "../escape", " foo", "foo ", "-foo", "foo-", "foo@main"]
)
def test_invalid_distribution_names(name: str) -> None:
    with pytest.raises(ValidationError):
        InstalledPluginConfig(distribution=name)


def test_catalog_loads_without_installing(
    config: LyraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("Catalog startup must not launch subprocesses")

    monkeypatch.setattr("subprocess.run", forbidden)
    catalog = registry.Catalog.load(config.plugins)
    assert len(catalog.discovery.search()) == 7


def test_disabled_plugins_need_not_be_installed(config: LyraConfig) -> None:
    config.plugins.installed.append(
        InstalledPluginConfig(distribution="missing-disabled", enabled=False)
    )
    catalog = plugins.load_installed_plugins(config.plugins)
    assert list(catalog.manifests) == ["lyra-smoke-plugin"]
    assert admin.list_plugins(make_runtime(config)).plugins[-1].version is None


def test_configuration_rejects_duplicate_normalized_distributions(
    config: LyraConfig,
) -> None:
    raw = config.model_dump()
    raw["plugins"]["installed"] = [
        {"distribution": "My_Plugin"},
        {"distribution": "my-plugin"},
    ]
    with pytest.raises(ValidationError, match="unique"):
        type(config).model_validate(raw)


def test_catalog_reads_use_captured_plugins(
    config: LyraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = registry.Catalog.load(config.plugins)

    def forbidden(_: object) -> None:
        pytest.fail("Catalog reads must not retry initialization")

    monkeypatch.setattr(registry, "load_installed_plugins", forbidden)
    assert catalog.discovery.search()
