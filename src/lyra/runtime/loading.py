"""Load plugin implementations matching their saved declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.manifests import MANIFEST_FILENAME
from lyra.sdk.loading import load_plugin_definition

if TYPE_CHECKING:
    from lyra.contracts.plugins import PluginManifest


def load_bound_plugin(
    manifest: PluginManifest,
) -> BoundPlugin:
    """Load a factory matching its manifest.

    Returns:
        The verified plugin bound for execution.

    Raises:
        RuntimeError: If its contract is stale.
    """
    definition = load_plugin_definition(manifest.factory)
    live_manifest = definition.manifest(
        plugin=manifest.plugin,
        factory=manifest.factory,
    )
    if live_manifest.model_dump(mode="json") != manifest.model_dump(mode="json"):
        msg = (
            f"Plugin factory {manifest.factory!r} does not match {MANIFEST_FILENAME}. "
            "Run 'lyra-plugin build-manifest' in the plugin project."
        )
        raise RuntimeError(msg)
    return BoundPlugin(definition)
