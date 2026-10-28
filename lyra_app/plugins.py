"""Read installed plugin metadata and manifests without importing plugin code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.sdk.config import PluginsConfig
    from lyra.sdk.models.plugin import PluginManifest

from lyra.sdk.plugin_loader import load_plugin_manifest


@dataclass(frozen=True)
class InstalledPlugin:
    """Pair a selected distribution with its validated manifest and routing."""

    distribution: str
    manifest: PluginManifest
    metric_queues: dict[str, str]


def load_installed_plugins(config: PluginsConfig) -> list[InstalledPlugin]:
    """Load enabled manifests and resolve their queue assignments.

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
        manifest = load_plugin_manifest(
            plugin.distribution, manifest_path=plugin.manifest_path
        )
        names = {metric.name for metric in manifest.nodes if metric.role == "metric"}
        duplicates = metric_names & names
        if duplicates:
            msg = "Duplicate metric names in plugin manifests: " + ", ".join(
                sorted(duplicates)
            )
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
        plugins.append(InstalledPlugin(plugin.distribution, manifest, queues))
    return plugins
