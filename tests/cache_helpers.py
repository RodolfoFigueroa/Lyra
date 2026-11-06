"""Read the value from an optional computation-cache observation."""

from typing import TypeVar

from lyra.sdk.cache import CachedArtifact

Value = TypeVar("Value")


def cached_value(item: CachedArtifact[Value] | None) -> Value | None:
    return item.value if item is not None else None
