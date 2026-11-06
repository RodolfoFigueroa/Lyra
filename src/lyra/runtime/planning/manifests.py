"""Read installed plugin metadata and manifests without loading implementations."""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

from lyra.contracts.config import normalize_distribution_name
from lyra.contracts.plugins import PluginManifest

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
