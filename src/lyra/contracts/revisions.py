"""Caller-controlled cache revision contract."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import StringConstraints

from lyra.contracts.locations import LocationContractReference

CacheRevision = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r"\S")
]
"""Exact nonblank caller revision, at most 256 characters."""


@dataclass(frozen=True)
class RevisionBinding:
    """A pinned dataset identity; None explicitly means unversioned."""

    contract: LocationContractReference
    revision: str | None


@dataclass(frozen=True)
class RevisionPlan:
    """Separate native calculation bindings from selected-route bindings."""

    calculation: frozenset[RevisionBinding]
    selection: frozenset[RevisionBinding]

    @property
    def bindings(self) -> frozenset[RevisionBinding]:
        """All bindings that preparation must pin."""
        return self.calculation | self.selection
