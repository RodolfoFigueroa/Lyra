"""Load enabled live plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.contracts.config import PluginsConfig
    from lyra.sdk.plugin import PluginDefinition

from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.sdk.loading import load_installed_plugin_definition


@dataclass(frozen=True)
class InstalledPlugin:
    """Pair a selected distribution with its live definition."""

    distribution: str
    definition: PluginDefinition


def load_installed_plugins(
    config: PluginsConfig, *, build_revision: str | None = None
) -> CompiledCatalog:
    """Compile the installed definitions together.

    Returns:
        The compiled catalog.
    """
    installed = discover_installed_plugins(config)
    return compile_live_catalog(
        {plugin.distribution: plugin.definition for plugin in installed},
        build_revision=build_revision,
    )


def discover_installed_plugins(config: PluginsConfig) -> list[InstalledPlugin]:
    """Load enabled definitions.

    Returns:
        Validated installed plugins in configuration order.

    Raises:
        ValueError: If distributions or metrics conflict.
    """
    plugins: list[InstalledPlugin] = []
    distributions: set[str] = set()
    metric_names: set[str] = set()
    for plugin in config.installed:
        if plugin.distribution in distributions:
            msg = f"Duplicate plugin distribution: {plugin.distribution!r}"
            raise ValueError(msg)
        distributions.add(plugin.distribution)
        if not plugin.enabled:
            continue
        definition = load_installed_plugin_definition(plugin.distribution)
        names = set(definition.metric_names)
        duplicates = metric_names & names
        if duplicates:
            msg = "Duplicate metric names in plugins: " + ", ".join(sorted(duplicates))
            raise ValueError(msg)
        metric_names.update(names)
        plugins.append(InstalledPlugin(plugin.distribution, definition))
    return plugins
