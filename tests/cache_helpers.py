"""Read the value from an optional computation-cache observation."""

from typing import TypeVar

import pandera.pandas as pa

from lyra.runtime.cache import CachedArtifact

Value = TypeVar("Value")


def cached_value(item: CachedArtifact[Value] | None) -> Value | None:
    return item.value if item is not None else None


SCHEMA = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)
