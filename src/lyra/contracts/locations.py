"""Serializable location declarations, independent of runtime location values.

Providers carry structural metadata compiled from LocationOutputSchema. Executable
Pandera checks stay with that schema; serialized metadata never executes code.
"""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from lyra.contracts.outputs import LocationOutputMetadata
from lyra.contracts.provider_scope import DatasetScope, EffectiveScope, ProviderScope
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
    """Select opaque record IDs from one provider.

    IDs retain their spelling, order, and duplicates. Resolution deduplicates them
    while preserving first-occurrence order.
    """

    model_config = ConfigDict(strict=True)

    provider: Annotated[str, Field(min_length=1)]
    ids: list[Annotated[str, Field(min_length=1)]]


Name = Annotated[str, Field(min_length=1)]


class LocationContractReference(StrictBaseModel):
    """Concrete source identity with captured provider scope."""

    model_config = ConfigDict(frozen=True)

    provider_scope: DatasetScope

    @property
    def provider_id(self) -> str:
        """Registered provider owning these records."""
        return self.provider_scope.provider_id

    @property
    def declaration(self) -> Self:
        """The underlying location contract used to validate handler bindings."""
        if isinstance(self.provider_scope, EffectiveScope):
            return self.model_copy(update={"provider_scope": self.provider_scope.base})
        return self

    @property
    def identity_payload(self) -> dict[str, object]:
        """Canonical identity without provider or origin descriptions."""
        return {"provider_scope": self.provider_scope.identity_payload}


class LocationUnitIdentity(StrictBaseModel):
    """An opaque record ID in a pinned dataset."""

    model_config = ConfigDict(frozen=True)
    provider_scope: DatasetScope
    id: Name


class LocationContract(StrictBaseModel):
    """Provider scope, declared record structure, and dataset metadata."""

    provider_scope: ProviderScope
    structure: LocationOutputMetadata
    data_revision: Name | None = None
    meaning: Name
    observation_period: Name | None = None
    release: Name | None = None
    field_definitions: dict[str, Name] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        """Require distinct declared ID and geometry columns.

        Returns:
            The validated contract.

        Raises:
            ValueError: If structural column conventions are invalid.
        """
        names = {column.name for column in self.structure.output.columns}
        if (
            self.structure.id_column not in names
            or self.structure.output.geometry is None
            or self.structure.id_column == self.structure.output.geometry
        ):
            message = (
                "Provider records require distinct declared ID and geometry columns"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        """Validate source definitions against the shared columns.

        Returns:
            The validated contract.

        Raises:
            ValueError: If a definition names an undeclared field.
        """
        names = {column.name for column in self.structure.output.columns}
        if self.field_definitions.keys() - names:
            message = "Field definitions must name declared columns"
            raise ValueError(message)
        return self

    @property
    def reference(self) -> LocationContractReference:
        """Concrete binding retaining the captured provider scope."""
        return LocationContractReference(
            provider_scope=self.provider_scope,
        )

    def unit_identity(self, identifier: str) -> LocationUnitIdentity:
        """Return the geographic identity of an opaque unit ID."""
        return LocationUnitIdentity(
            provider_scope=self.provider_scope,
            id=identifier,
        )
