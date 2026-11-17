"""Captured declaration identities for provider records.

These values contain no acquisition state. A fixed scope promises fixed record
contents and complete dataset membership; Lyra does not audit that promise by
comparing data. Scope is captured from declarations, never from frame metadata.
"""

import json
from typing import Annotated

from pydantic import ConfigDict, Field, field_serializer
from typing_extensions import override

from lyra.contracts.origins import Origin
from lyra.contracts.revision_tokens import ImplementationRevision
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.value_identity import value_key


class ProviderScope(StrictBaseModel):
    """Identify a provider's fixed dataset independently of resource bindings.

    Origins are copied into an unordered immutable collection, deduplicated by
    external identity. Their descriptions remain available as metadata; equivalent
    descriptions need not be unique. Provider and origin descriptions are retained
    metadata and do not affect equality, hashing, or execution identity.
    None is a stable omitted code revision.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    provider_id: Annotated[str, Field(min_length=1)]
    code_revision: ImplementationRevision | None = None
    origins: Annotated[frozenset[Origin], Field(strict=False)] = frozenset()
    description: str | None = None

    @override
    def __eq__(self, other: object) -> bool:
        """Return whether provider identities match, independent of descriptions."""
        if not isinstance(other, ProviderScope):
            return NotImplemented
        return (
            self.provider_id,
            self.code_revision,
            self.origins,
        ) == (other.provider_id, other.code_revision, other.origins)

    @override
    def __hash__(self) -> int:
        """Return a hash of the same identity fields used for equality."""
        return hash((self.provider_id, self.code_revision, self.origins))

    @field_serializer("origins")
    @staticmethod
    def serialize_origins(origins: frozenset[Origin]) -> list[dict[str, str | None]]:
        """Return transport-safe origin records in deterministic order."""
        return sorted(
            (origin.model_dump() for origin in origins),
            key=lambda item: json.dumps(item, sort_keys=True),
        )

    @property
    def identity_payload(self) -> dict[str, object]:
        """Canonical declaration identity, independent of descriptions."""
        return {
            "provider_id": self.provider_id,
            "code_revision": self.code_revision,
            "origins": sorted(
                (
                    {"id": origin.id, "revision": origin.revision}
                    for origin in self.origins
                ),
                key=lambda item: json.dumps(item, sort_keys=True),
            ),
        }


class RecordIdentity(StrictBaseModel):
    """Identify one base record within a captured provider scope."""

    model_config = ConfigDict(strict=True, frozen=True)

    scope: ProviderScope
    location_id: Annotated[str, Field(min_length=1)]

    @property
    def identity_payload(self) -> dict[str, object]:
        """The record descriptor without acquisition metadata."""
        return {
            "kind": "record",
            "scope": self.scope.identity_payload,
            "location_id": self.location_id,
        }

    @property
    def key(self) -> str:
        """The immutable record's SHA-256 value key."""
        return value_key(self.identity_payload)
