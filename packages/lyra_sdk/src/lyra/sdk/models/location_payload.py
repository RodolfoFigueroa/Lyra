"""ID selections and internal resolved location data."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Annotated, Self

import pyproj
from lyra.sdk.location_frames import frame_snapshot, locations_frame
from lyra.sdk.models.geometry import CRS, Feature
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import (
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pyproj.exceptions import CRSError

if TYPE_CHECKING:
    import geopandas as gpd

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

    @classmethod
    def from_geodataframe(
        cls,
        frame: gpd.GeoDataFrame,
        *,
        collection: str,
        unit_type: str,
        id_column: str,
    ) -> Self:
        """Convert ordered rows using explicit identity, active geometry, and CRS.

        Every other column becomes a property. Select input columns before calling.
        Indexes and source dtypes are not retained; dates become ISO strings and
        missing typed scalars become null. In object columns, pd.NA denotes an
        absent property and None denotes explicit null.
        Geometry is never repaired or reprojected.

        Returns:
            An independently validated value in the supplied scope.
        """
        return cls.model_validate(
            frame_snapshot(
                frame, collection=collection, unit_type=unit_type, id_column=id_column
            ).to_dict()
        )

    def to_geodataframe(
        self, *, id_column: str = "id", geometry_column: str = "geometry"
    ) -> gpd.GeoDataFrame:
        """Return an independent frame with ordered features, properties, and CRS.

        Property columns use object dtype; pd.NA denotes absence and None null.
        Empty values
        contain only the ID and geometry columns; declared schema reconstruction
        is separate from this conversion.

        Returns:
            A new frame using the requested identity and active geometry names.
        """
        return locations_frame(
            self, id_column=id_column, geometry_column=geometry_column
        )

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
