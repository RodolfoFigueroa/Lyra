"""Read installed distribution versions without importing plugins."""

from importlib import metadata


def installed_version(distribution: str) -> str | None:
    """Return installed distribution metadata without importing its modules."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
