"""Load enabled live plugins and validate application queue routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.contracts.config import PluginsConfig
    from lyra.contracts.plugins import PluginManifest
    from lyra.sdk.plugin import PluginDefinition

from lyra.contracts.plugins import PluginInfo
from lyra.runtime.planning.providers import select_location_provider
from lyra.sdk.loading import load_installed_plugin_definition


@dataclass(frozen=True)
class InstalledPlugin:
    """Pair a selected distribution with its live definition and routing."""

    distribution: str
    definition: PluginDefinition
    metric_queues: dict[str, str]

    @property
    def manifest(self) -> PluginManifest:
        """Derive a serializable description from the live registration."""
        return self.definition.manifest(
            plugin=PluginInfo(name=self.distribution, version="live"),
            factory="live:create_plugin",
        )


def load_installed_plugins(config: PluginsConfig) -> list[InstalledPlugin]:
    """Load enabled definitions and resolve their queue assignments.

    Returns:
        Validated installed plugins in configuration order.

    Raises:
        ValueError: If distributions, metrics, or queue overrides conflict.
    """
    plugins: list[InstalledPlugin] = []
    distributions: set[str] = set()
    metric_names: set[str] = set()
    resources: dict[str, dict[str, object]] = {}
    for plugin in config.installed:
        if plugin.distribution in distributions:
            msg = f"Duplicate plugin distribution: {plugin.distribution!r}"
            raise ValueError(msg)
        distributions.add(plugin.distribution)
        if not plugin.enabled:
            continue
        definition = load_installed_plugin_definition(plugin.distribution)
        manifest = definition.manifest(
            plugin=PluginInfo(name=plugin.distribution, version="live"),
            factory="live:create_plugin",
        )
        for name, declaration in manifest.resources.items():
            if name in resources and resources[name] != declaration:
                message = f"Conflicting resource declarations: {name}"
                raise ValueError(message)
            resources[name] = declaration
        names = {metric.name for metric in manifest.nodes if metric.kind == "metric"}
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
    select_location_provider(
        {plugin.distribution: plugin.manifest.location_provider for plugin in plugins}
    )
    return plugins
