"""Opaque implementation tokens."""

from typing import Annotated

from pydantic import StringConstraints

ImplementationRevision = Annotated[
    str, StringConstraints(strict=True, min_length=1, pattern=r"\S")
]
"""Exact nonblank author-owned implementation revision token."""
