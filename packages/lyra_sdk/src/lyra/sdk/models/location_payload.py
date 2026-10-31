"""ID selections and internal resolved location data."""

from __future__ import annotations

import math
from typing import Annotated, Self

import pyproj
from lyra.sdk.models.geometry import CRS, Feature
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import (
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pyproj.exceptions import CRSError

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


class LocationPayload(StrictBaseModel):
    """Temporary validated payload for location boundaries and scalar metadata.

    Properties contain finite JSON scalars. Public locations discard this
    mutable representation after constructing their owned snapshot.
    """

    collection: CollectionName
    unit_type: UnitType
    crs: CRS
    features: list[Feature]

    @property
    def ids(self) -> list[str]:
        """Feature IDs in collection order, returned as an independent list."""
        return [feature.id for feature in self.features]

    @field_validator("crs")
    @classmethod
    def known_crs(cls, crs: CRS) -> CRS:
        """Validate the CRS without rewriting it or reprojecting.

        Returns:
            The validated CRS with its original identifier.

        Raises:
            ValueError: If the CRS structure or identifier is invalid.
        """
        crs = CRS.model_validate(crs.model_dump())
        try:
            pyproj.CRS.from_user_input(crs.properties.name)
        except CRSError as exc:
            message = f"Unknown CRS: {crs.properties.name!r}"
            raise ValueError(message) from exc
        return crs

    @field_validator("features")
    @classmethod
    def validated_features(cls, features: list[Feature]) -> list[Feature]:
        """Revalidate feature objects and require scalar properties.

        Returns:
            Validated features in their original order.

        Raises:
            ValueError: If a property is not a finite JSON scalar.
        """
        validated = []
        for source in features:
            feature = Feature.model_validate(source.model_dump())
            for column, value in feature.properties.items():
                if type(value) not in {str, bool, int, float, type(None)} or (
                    isinstance(value, float) and not math.isfinite(value)
                ):
                    message = (
                        f"Feature {feature.id!r} property {column!r} must be a "
                        "finite JSON scalar (string, boolean, number, or null)"
                    )
                    raise ValueError(message)
            validated.append(feature)
        return validated

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        """Validate feature identity within this scope.

        Returns:
            This validated value.

        Raises:
            ValueError: If feature IDs repeat.
        """
        if len(set(self.ids)) != len(self.features):
            message = "Location feature IDs must be unique"
            raise ValueError(message)
        return self
