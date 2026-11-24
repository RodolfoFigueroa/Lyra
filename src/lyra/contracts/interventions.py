"""Strict inline interventions and independently retained accepted records."""

from copy import deepcopy
from typing import Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import (
    EffectiveScope,
    InterventionID,
    InterventionIdentity,
    ProviderScope,
)
from lyra.contracts.value_identity import check_json_value


class InterventionSpec(BaseModel):
    """Caller JSON records interpreted only with a registered provider schema."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: InterventionID
    catalog: InterventionID
    revision: InterventionID
    add: list[dict[str, JsonValue]] = Field(default_factory=list)
    remove: list[InterventionID] = Field(default_factory=list)

    @field_validator("add", "remove", mode="before")
    @classmethod
    def capture_actions(cls, value: object) -> object:
        """Return detached JSON actions without accepting non-JSON values."""
        check_json_value(value)
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_actions(self) -> Self:
        """Return a nonempty set with unique removal and addition IDs.

        Raises:
            ValueError: If actions are empty or repeat an identifier.
        """
        if not self.add and not self.remove:
            msg = "An intervention must contain at least one addition or removal"
            raise ValueError(msg)
        if len(set(self.remove)) != len(self.remove):
            msg = "Repeated removal IDs are not allowed"
            raise ValueError(msg)
        ids = [row.get("location_id") for row in self.add]
        if any(type(value) is not str or not value for value in ids):
            msg = "Added location_id values must be nonempty strict strings"
            raise ValueError(msg)
        if len(set(ids)) != len(ids):
            msg = "Duplicate added IDs are not allowed"
            raise ValueError(msg)
        return self


class AcceptedIntervention(InterventionSpec):
    """Owned records with descriptive schema for registration-free inspection.

    Executable provider checks are applied at acceptance, not reconstructed from
    this descriptive metadata. Caller JSON must use InterventionSpec instead.
    """

    addition_schema: OutputMetadata

    def submission_payload(self) -> dict:
        """Return the canonical public actions for worker revalidation."""
        return self.model_dump(include=set(InterventionSpec.model_fields))

    @property
    def identity(self) -> InterventionIdentity:
        """Caller-supplied identity shared by validation and effective datasets."""
        return InterventionIdentity(
            catalog=self.catalog, id=self.id, revision=self.revision
        )

    def effective_scope(self, base: ProviderScope) -> EffectiveScope:
        """Return the unchanged base declaration qualified by this immutable set."""
        return EffectiveScope(base=base, intervention=self.identity)

    @property
    def added_ids(self) -> frozenset[str]:
        """IDs owned by this set, independent of submitted order."""
        return frozenset(cast("str", row["location_id"]) for row in self.add)

    @field_validator("addition_schema", mode="before")
    @classmethod
    def capture_schema(cls, value: object) -> object:
        """Return independent descriptive metadata."""
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_schema(self) -> Self:
        """Return accepted records with their resolved spatial declaration.

        Raises:
            ValueError: If actions or their spatial declaration are inconsistent.
        """
        schema = self.addition_schema
        if schema.geometry != "geometry" or not any(
            column.name == "geometry" and column.parameters.get("crs")
            for column in schema.columns
        ):
            msg = "Accepted additions require their resolved schema and CRS"
            raise ValueError(msg)
        return self


def validate_intervention_catalogs(
    values: list[InterventionSpec],
) -> list[InterventionSpec]:
    """Return sets with distinct target catalogs, without reordering values.

    Raises:
        ValueError: If multiple sets target the same catalog.
    """
    catalogs = [value.catalog for value in values]
    if len(set(catalogs)) != len(catalogs):
        msg = "Only one intervention set per catalog is allowed"
        raise ValueError(msg)
    return values
