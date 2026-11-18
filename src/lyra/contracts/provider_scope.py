"""Captured declaration identities for provider records.

These values contain no acquisition state. A fixed scope promises fixed record
contents and complete dataset membership; Lyra does not audit that promise by
comparing data. Scope is captured from declarations, never from frame metadata.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import ConfigDict, Field, field_serializer, model_validator
from typing_extensions import override

from lyra.contracts.origins import Origin
from lyra.contracts.revision_tokens import ImplementationRevision
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.value_identity import value_key

InterventionID = Annotated[str, Field(strict=True, min_length=1)]


class InterventionIdentity(StrictBaseModel):
    """Caller promise identifying immutable changes for one catalog."""

    model_config = ConfigDict(strict=True, frozen=True)
    catalog: InterventionID
    id: InterventionID
    revision: InterventionID


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


class EffectiveScope(StrictBaseModel):
    """A request's view of an unchanged base declaration."""

    model_config = ConfigDict(strict=True, frozen=True)

    base: ProviderScope
    intervention: InterventionIdentity

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        """Return a scope qualified by a set targeting the same catalog.

        Raises:
            ValueError: If the set belongs to another provider.
        """
        if self.intervention.catalog != self.base.provider_id:
            msg = "Intervention catalog differs from the base provider"
            raise ValueError(msg)
        return self

    @property
    def validation_key(self) -> str:
        """Address of the persisted complete membership check."""
        change = self.intervention
        return value_key(
            [self.base.identity_payload, change.catalog, change.id, change.revision]
        )

    @property
    def provider_id(self) -> str:
        """Public ID of the underlying registered provider."""
        return self.base.provider_id

    @property
    def origins(self) -> frozenset[Origin]:
        """Declared external origins of the base records."""
        return self.base.origins

    @property
    def code_revision(self) -> ImplementationRevision | None:
        """Declared implementation revision of the base provider."""
        return self.base.code_revision

    @property
    def description(self) -> str | None:
        """Captured descriptive metadata of the base provider."""
        return self.base.description

    @property
    def identity_payload(self) -> dict[str, object]:
        """Canonical identity of the base dataset and applicable changes."""
        return {
            "base": self.base.identity_payload,
            "intervention": self.intervention.model_dump(),
        }


DatasetScope = ProviderScope | EffectiveScope


class RecordIdentity(StrictBaseModel):
    """Identify one immutable base or added record by its owning scope."""

    model_config = ConfigDict(strict=True, frozen=True)

    scope: DatasetScope
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


@dataclass(frozen=True)
class RecordReferences:
    """Dataset scope and the added IDs needed to address a selection's records."""

    scope: DatasetScope
    added_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Capture added membership independently of its producer.

        Raises:
            ValueError: If additions lack an intervention identity.
        """
        if self.added_ids and not isinstance(self.scope, EffectiveScope):
            msg = "Added record references require an effective scope"
            raise ValueError(msg)
        object.__setattr__(self, "added_ids", frozenset(self.added_ids))

    @property
    def base(self) -> ProviderScope:
        """Declaration under which the typed record schema was registered."""
        return self.scope.base if isinstance(self.scope, EffectiveScope) else self.scope

    def by_key(self, ids: Iterable[str]) -> dict[str, RecordIdentity]:
        """Address each selected ID under its immutable record owner.

        Returns:
            Qualified references suitable for a grouped read or publication.
        """
        base = self.base
        result = {}
        for location in dict.fromkeys(ids):
            reference = RecordIdentity(
                scope=self.scope if location in self.added_ids else base,
                location_id=location,
            )
            result[reference.key] = reference
        return result
