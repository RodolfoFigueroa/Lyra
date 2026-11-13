"""Immutable declarations of external data identity."""

from typing import Annotated

from pydantic import ConfigDict, Field
from typing_extensions import override

from lyra.contracts.strict import StrictBaseModel


class Origin(StrictBaseModel):
    """Identify fixed external contents by an opaque ID and revision.

    None is a stable unversioned revision. Authors must change the identity when
    external contents change. Description is metadata and does not affect equality.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    id: Annotated[str, Field(min_length=1)]
    revision: str | None = None
    description: str | None = None

    @property
    def identity(self) -> tuple[str, str | None]:
        """The external identity, independent of descriptive metadata."""
        return self.id, self.revision

    @override
    def __eq__(self, other: object) -> bool:
        """Return whether the external identities match."""
        if not isinstance(other, Origin):
            return NotImplemented
        return self.identity == other.identity

    @override
    def __hash__(self) -> int:
        """Return a hash of the same fields used for equality."""
        return hash(self.identity)
