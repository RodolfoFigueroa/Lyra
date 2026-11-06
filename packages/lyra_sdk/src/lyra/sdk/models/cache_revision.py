"""Caller-controlled cache revision contract."""

from typing import Annotated

from pydantic import StringConstraints

CacheRevision = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r"\S")
]
"""Exact nonblank caller revision, at most 256 characters."""
