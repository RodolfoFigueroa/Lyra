"""Serializable location declarations, independent of runtime location values.

Families share structural metadata compiled from LocationOutputSchema. Executable
Pandera checks stay with that schema; serialized metadata never executes code.
"""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from lyra.contracts.outputs import LocationOutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.strict import StrictBaseModel

CollectionName = Annotated[
    str,
    Field(
        min_length=1,
        description="Internal collection identifier.",
    ),
]
UnitType = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]


class LocationSelection(StrictBaseModel):
    """Select opaque unit IDs within one collection and unit type.

    IDs retain their spelling, order, and duplicates. Resolution deduplicates them
    while preserving first-occurrence order.
    """

    model_config = ConfigDict(strict=True)

    provider: Annotated[str, Field(min_length=1)]
    ids: list[Annotated[str, Field(min_length=1)]]


Name = Annotated[str, Field(min_length=1)]


class LocationContractReference(StrictBaseModel):
    """Concrete source identity with optional captured provider scope."""

    model_config = ConfigDict(frozen=True)

    provider_scope: ProviderScope | None = None
    collection: CollectionName
    unit_type: UnitType
    source: Name
    geographical_edition: Name
    edition: Name | None = None

    @property
    def identity_payload(self) -> dict[str, object]:
        """Canonical identity without origin descriptions."""
        result = self.model_dump(mode="json")
        if self.provider_scope is not None:
            result["provider_scope"] = self.provider_scope.identity_payload
        return result


class LocationUnitIdentity(StrictBaseModel):
    """Location identity within geographic and optional provider scope."""

    model_config = ConfigDict(frozen=True)

    provider_scope: ProviderScope | None = None
    collection: CollectionName
    unit_type: UnitType
    geographical_edition: Name
    id: Name


class LocationFamily(StrictBaseModel):
    """Shared structural contract for a collection and unit type."""

    collection: CollectionName
    unit_type: UnitType
    structure: LocationOutputMetadata

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        """Require distinct declared ID and geometry columns.

        Returns:
            The validated family.

        Raises:
            ValueError: If structural column conventions are invalid.
        """
        names = {column.name for column in self.structure.output.columns}
        if (
            self.structure.id_column not in names
            or self.structure.output.geometry is None
            or self.structure.id_column == self.structure.output.geometry
        ):
            message = "Family requires distinct declared ID and geometry columns"
            raise ValueError(message)
        return self


class LocationContract(StrictBaseModel):
    """Concrete dataset binding and meaning under a shared family schema.

    An absent edition is implicit; an absent revision is unversioned. Source
    identifies the concrete observation contract, including distinct periods or
    releases even when geographic units coincide. Field definitions describe
    source meaning without changing the family's structural validation rules.
    """

    provider_scope: ProviderScope | None = None
    family: LocationFamily
    source: Name
    geographical_edition: Name
    edition: Name | None = None
    data_revision: Name | None = None
    meaning: Name
    observation_period: Name | None = None
    release: Name | None = None
    field_definitions: dict[str, Name] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        """Validate source definitions against the shared columns.

        Returns:
            The validated contract.

        Raises:
            ValueError: If a definition names an undeclared field.
        """
        names = {column.name for column in self.family.structure.output.columns}
        if self.field_definitions.keys() - names:
            message = "Field definitions must name declared columns"
            raise ValueError(message)
        return self

    @property
    def reference(self) -> LocationContractReference:
        """Concrete binding retaining the captured provider scope."""
        return LocationContractReference(
            provider_scope=self.provider_scope,
            collection=self.family.collection,
            unit_type=self.family.unit_type,
            source=self.source,
            geographical_edition=self.geographical_edition,
            edition=self.edition,
        )

    def unit_identity(self, identifier: str) -> LocationUnitIdentity:
        """Return the geographic identity of an opaque unit ID."""
        return LocationUnitIdentity(
            provider_scope=self.provider_scope,
            collection=self.family.collection,
            unit_type=self.family.unit_type,
            geographical_edition=self.geographical_edition,
            id=identifier,
        )
