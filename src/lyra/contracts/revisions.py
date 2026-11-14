"""Author, deployment, and caller revision contracts."""

from dataclasses import dataclass

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.revision_tokens import (
    BuildRevision,
    CacheRevision,
    ImplementationRevision,
)

__all__ = [
    "BuildRevision",
    "CacheRevision",
    "ImplementationRevision",
    "RevisionBinding",
    "RevisionPlan",
]


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
