"""Captured declaration identities for provider records.

These values contain no acquisition state. A fixed scope promises fixed record
contents and complete dataset membership; Lyra does not audit that promise by
comparing data. Scope is captured from declarations, never from frame metadata.
"""

import json
from typing import Annotated

from pydantic import ConfigDict, Field, field_serializer

from lyra.contracts.origins import Origin
from lyra.contracts.strict import StrictBaseModel


class ProviderScope(StrictBaseModel):
    """Identify a provider's fixed dataset independently of resource bindings.

    Origins are copied into an unordered immutable collection, deduplicated by
    external identity. Their descriptions remain available as metadata; equivalent
    descriptions need not be unique. None is a stable omitted code revision.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    provider_id: Annotated[str, Field(min_length=1)]
    code_revision: str | None = None
    origins: Annotated[frozenset[Origin], Field(strict=False)] = frozenset()

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
