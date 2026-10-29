"""ID selections and internal resolved location data."""

from typing import Annotated

from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import ConfigDict, Field

CollectionName = Annotated[
    str,
    Field(
        pattern=r"^[^\s:]+:[^\s:]+$",
        description="Namespaced collection: namespace:name.",
    ),
]
UnitType = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]


class LocationSelection(StrictBaseModel):
    """Select opaque unit IDs within one collection and unit type.

    IDs retain their spelling, order, and duplicates. Resolution deduplicates them
    while preserving first-occurrence order.
    """

    model_config = ConfigDict(strict=True)

    collection: CollectionName
    unit_type: UnitType
    ids: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class ResolvedLocation(StrictBaseModel):
    """Internal scoped features for invocation preparation and serialization.

    Pass only location (the handler's LocationInput) to metric handlers.
    """

    collection: CollectionName
    unit_type: UnitType
    location: GeoJSON
