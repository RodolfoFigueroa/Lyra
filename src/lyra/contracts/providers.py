"""Serializable location provider declarations."""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from lyra.contracts.locations import CollectionName, LocationContract, UnitType
from lyra.contracts.outputs import LocationOutputMetadata
from lyra.contracts.strict import StrictBaseModel


class LocationCollection(StrictBaseModel):
    """Describe a collection without acquiring clients or reading credentials.

    Unit types are independent provider-defined categories. The CRS identifier
    labels every geometry returned for this collection; providers supply it on
    the returned GeoDataFrame.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    name: CollectionName
    unit_types: tuple[UnitType, ...] = Field(min_length=1)
    crs: Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
    outputs: dict[str, LocationOutputMetadata] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_unit_types(self) -> Self:
        """Reject repeated unit types.

        Returns:
            The validated declaration.

        Raises:
            ValueError: If unit types repeat.
        """
        if len(set(self.unit_types)) != len(self.unit_types):
            message = "Collection unit types must be unique"
            raise ValueError(message)
        return self


class LocationProviderManifest(StrictBaseModel):
    """Service-free provider declaration referencing a registered resource."""

    resource: str = Field(min_length=1, pattern=r"^\S+$")
    collections: tuple[LocationCollection, ...] = Field(min_length=1)
    contracts: tuple[LocationContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_collections(self) -> Self:
        """Return metadata with unique collection names.

        Raises:
            ValueError: If collection names repeat.
        """
        names = [collection.name for collection in self.collections]
        if len(names) != len(set(names)):
            message = "Provider collection names must be unique"
            raise ValueError(message)
        scopes = {
            (item.name, unit) for item in self.collections for unit in item.unit_types
        }
        families = {}
        references = set()
        for contract in self.contracts:
            key = (contract.family.collection, contract.family.unit_type)
            if key not in scopes or contract.reference in references:
                message = "Undeclared or duplicate provider contract"
                raise ValueError(message)
            references.add(contract.reference)
            if key in families and families[key] != contract.family:
                message = "Conflicting provider family structures"
                raise ValueError(message)
            families[key] = contract.family
        if set(families) != scopes:
            message = "Every provider scope requires a concrete contract"
            raise ValueError(message)
        return self
