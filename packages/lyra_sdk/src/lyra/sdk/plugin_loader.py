"""Import and validate plugin definitions from installed distributions."""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from importlib import metadata
from pathlib import Path

from lyra.sdk.config import normalize_distribution_name
from lyra.sdk.models.plugin import PluginManifest
from lyra.sdk.plugin import PluginDefinition

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FACTORY_PATTERN = re.compile(
    rf"^(?P<module>{_IDENTIFIER}(?:\.{_IDENTIFIER})*):(?P<attribute>{_IDENTIFIER})$"
)


class PluginLoadError(RuntimeError):
    """Raised when a configured plugin factory cannot produce a definition."""


def load_plugin_definition(factory_ref: str) -> PluginDefinition:
    """Import and invoke one synchronous, parameterless plugin factory.

    Returns:
        The plugin definition produced by the referenced factory.

    Raises:
        PluginLoadError: If the reference, factory, or returned definition is invalid.
    """
    match = _FACTORY_PATTERN.fullmatch(factory_ref)
    if match is None:
        msg = f"Plugin factory must use 'module:attribute' format: {factory_ref!r}"
        raise PluginLoadError(msg)

    try:
        module = importlib.import_module(match.group("module"))
        factory = getattr(module, match.group("attribute"))
    except (ImportError, AttributeError) as exc:
        msg = f"Could not import plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc

    if not callable(factory):
        msg = f"Plugin factory {factory_ref!r} must resolve to a callable"
        raise PluginLoadError(msg)
    if inspect.iscoroutinefunction(factory):
        msg = f"Plugin factory {factory_ref!r} must be synchronous"
        raise PluginLoadError(msg)
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError) as exc:
        msg = f"Could not inspect plugin factory {factory_ref!r}: {exc}"
        raise PluginLoadError(msg) from exc
    if signature.parameters:
        msg = f"Plugin factory {factory_ref!r} must declare no parameters"
        raise PluginLoadError(msg)

    try:
        definition = factory()
    except Exception as exc:
        msg = f"Plugin factory {factory_ref!r} failed: {exc}"
        raise PluginLoadError(msg) from exc
    if not isinstance(definition, PluginDefinition):
        msg = f"Plugin factory {factory_ref!r} must return PluginDefinition"
        raise PluginLoadError(msg)
    return definition


MANIFEST_FILENAME = "lyra.plugin.json"


def installed_version(distribution: str) -> str | None:
    """Return installed distribution metadata without importing its modules."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def installed_manifest_path(distribution: str) -> Path:
    """Return the conventional manifest path within the current environment."""
    return (
        Path(sys.prefix)
        / "share"
        / "lyra"
        / "plugins"
        / normalize_distribution_name(distribution)
        / MANIFEST_FILENAME
    )


def load_plugin_manifest(
    distribution: str, *, manifest_path: Path | None = None
) -> PluginManifest:
    """Read a manifest and verify its installed distribution identity.

    Returns:
        The validated manifest, without importing its factory.

    Raises:
        RuntimeError: If the package or manifest is missing or inconsistent.
    """
    distribution = normalize_distribution_name(distribution)
    version = installed_version(distribution)
    if version is None:
        msg = f"Plugin distribution {distribution!r} is not installed."
        raise RuntimeError(msg)
    path = manifest_path or installed_manifest_path(distribution)
    try:
        manifest = PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"Plugin manifest {path} is missing, unreadable, or invalid: {exc}"
        raise RuntimeError(msg) from exc
    if (
        normalize_distribution_name(manifest.plugin.name) != distribution
        or manifest.plugin.version != version
    ):
        msg = (
            f"Plugin manifest {path} identity does not match installed distribution "
            f"{distribution!r} version {version!r}."
        )
        raise RuntimeError(msg)
    return manifest


def load_validated_plugin_definition(
    manifest: PluginManifest,
) -> PluginDefinition:
    """Load a factory matching its manifest.

    Returns:
        The executable definition.

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
    return definition
