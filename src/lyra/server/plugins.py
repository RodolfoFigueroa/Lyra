"""Load enabled live plugins and validate application queue routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.contracts.config import PluginsConfig
    from lyra.contracts.plugins import PluginManifest
    from lyra.sdk.plugin import PluginDefinition

from lyra.contracts.plugins import PluginInfo
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.sdk.loading import load_installed_plugin_definition
from lyra.sdk.resources import resource_metadata


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
        for name, provider in definition.resources.items():
            declaration = resource_metadata(provider)
            if name in resources and resources[name] != declaration:
                message = f"Conflicting resource declarations: {name}"
                raise ValueError(message)
            resources[name] = declaration
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
    bound = bind_native_definitions(
        {plugin.distribution: plugin.definition for plugin in plugins}
    )
    return [
        replace(plugin, definition=bound[plugin.distribution]) for plugin in plugins
    ]
