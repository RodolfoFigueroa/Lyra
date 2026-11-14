"""Opaque implementation tokens and bounded deployment revisions."""

from typing import Annotated

from pydantic import StringConstraints

BuildRevision = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$",
    ),
]
"""Deployment-owned token covering code, declarations, and routing policy."""

ImplementationRevision = Annotated[
    str, StringConstraints(strict=True, min_length=1, pattern=r"\S")
]
"""Exact nonblank author-owned implementation revision token."""
