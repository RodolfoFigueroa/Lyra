"""Load enabled live plugins and validate application queue routing."""

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
    """Pair a selected distribution with its live definition and routing."""

    distribution: str
    definition: PluginDefinition
    metric_queues: dict[str, str]


def load_installed_plugins(
    config: PluginsConfig, *, build_revision: str | None = None
) -> tuple[CompiledCatalog, dict[str, str]]:
    """Compile the installed definitions together.

    Returns:
        The compiled catalog and its metric queue assignments.
    """
    installed = discover_installed_plugins(config)
    compiled = compile_live_catalog(
        {plugin.distribution: plugin.definition for plugin in installed},
        build_revision=build_revision,
    )
    queues = {
        name: queue
        for plugin in installed
        for name, queue in plugin.metric_queues.items()
    }
    return compiled, queues


def discover_installed_plugins(config: PluginsConfig) -> list[InstalledPlugin]:
    """Load enabled definitions and resolve their queue assignments.

    Returns:
        Validated installed plugins in configuration order.

    Raises:
        ValueError: If distributions, metrics, or queue overrides conflict.
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
        unknown = set(plugin.routing) - names
        if unknown:
            msg = (
                f"Plugin {plugin.distribution!r} has routing overrides "
                "for missing metrics: " + ", ".join(sorted(unknown))
            )
            raise ValueError(msg)
        queues = {
            name: plugin.routing.get(name, config.default_queue) for name in names
        }
        if set(queues.values()) - set(config.allowed_queues):
            msg = "Plugin routing queues must appear in plugins.allowed_queues"
            raise ValueError(msg)
        metric_names.update(names)
        plugins.append(InstalledPlugin(plugin.distribution, definition, queues))
    return plugins
