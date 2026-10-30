"""Public immutable location values."""

from __future__ import annotations

import json
import weakref
from typing import TYPE_CHECKING, Any, Self

from lyra.sdk import geometry_buffers, property_buffers
from lyra.sdk.location_frames import frame_snapshot
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.geometry import CRS, Feature
from lyra.sdk.models.location_payload import (
    CollectionName,
    LocationPayload,
    LocationSelection,
    UnitType,
)
from pydantic import (
    ConfigDict,
    ModelWrapValidatorHandler,
    PrivateAttr,
    SerializationInfo,
    model_serializer,
    model_validator,
)

if TYPE_CHECKING:
    import geopandas as gpd


__all__ = ["CollectionName", "LocationSelection", "Locations", "UnitType"]


_validated: dict[int, tuple[weakref.ReferenceType[Locations], LocationSnapshot]] = {}


def _remember(value: Locations, snapshot: LocationSnapshot) -> None:
    key = id(value)
    _validated[key] = (
        weakref.ref(value, lambda _: _validated.pop(key, None)),
        snapshot,
    )


def validated_locations(value: object) -> Locations:
    """Reuse owned contents only for an unchanged, validated instance.

    Returns:
        Locations with shared invariants established; schema checks remain separate.
    """
    entry = _validated.get(id(value))
    if (
        type(value) is Locations
        and entry is not None
        and not value.__dict__
        and value.__pydantic_private__ is not None
        and value.__pydantic_private__.get("_snapshot") is entry[1]
    ):
        return value
    return Locations.model_validate(value)


def location_fingerprint(value: Locations) -> str:
    """Return cached content identity after establishing snapshot ownership."""
    owned = validated_locations(value)
    return _validated[id(owned)][1].fingerprint


def location_snapshot(value: Locations) -> LocationSnapshot:
    """Return owned columns after validating the location boundary."""
    owned = validated_locations(value)
    return _validated[id(owned)][1]


def locations_from_snapshot(snapshot: LocationSnapshot) -> Locations:
    """Wrap internally validated owned columns without materializing them again.

    Returns:
        A public value sharing only immutable buffers with the snapshot.

    Raises:
        TypeError: If the input is not a concrete validated snapshot.
    """
    if type(snapshot) is not LocationSnapshot:
        message = "Expected a validated LocationSnapshot"
        raise TypeError(message)
    value = Locations.model_construct()
    value.__dict__.clear()
    value.__pydantic_private__ = {"_snapshot": snapshot}
    _remember(value, snapshot)
    return value


class Locations(LocationPayload):
    """Immutable snapshot-backed locations with independent mutable exports."""

    __hash__ = None

    model_config = ConfigDict(frozen=True, revalidate_instances="always")
    _snapshot: LocationSnapshot | None = PrivateAttr(default=None)

    @classmethod
    def from_geodataframe(
        cls, frame: gpd.GeoDataFrame, *, collection: str, unit_type: str, id_column: str
    ) -> Self:
        """Own frame contents; pd.NA denotes absence and None denotes null.

        Returns:
            Validated snapshot-backed locations in the supplied scope.
        """
        snapshot = frame_snapshot(
            frame, collection=collection, unit_type=unit_type, id_column=id_column
        )
        value = cls.model_construct()
        value.__dict__.clear()
        value.__pydantic_private__ = {"_snapshot": snapshot}
        _remember(value, snapshot)
        return value

    @model_validator(mode="wrap")
    @classmethod
    def external_data(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Revalidate public instances, including bypassed construction.

        Returns:
            Validated locations without granting model instances trust.

        Raises:
            ValueError: If bypassed model fields contain invalid model types.
        """
        if isinstance(value, Locations):
            try:
                fields = {
                    name: getattr(value, name) for name in LocationPayload.model_fields
                }
            except AttributeError as exc:
                message = "Location model is missing required fields"
                raise ValueError(message) from exc
            if (
                not isinstance(fields["crs"], CRS)
                or not isinstance(fields["features"], list)
                or any(
                    not isinstance(feature, Feature) for feature in fields["features"]
                )
            ):
                message = "Location model fields require CRS and feature models"
                raise ValueError(message)
            value = fields
        return handler(value)

    @model_validator(mode="after")
    def own_snapshot(self) -> Self:
        """Own validated columns and discard temporary feature storage.

        Returns:
            The snapshot-backed value.
        """
        features = self.features
        snapshot = LocationSnapshot(
            geometry_buffers.encode(feature.geometry for feature in features),
            property_buffers.encode(
                (feature.id for feature in features),
                (feature.properties for feature in features),
                collection=self.collection,
                unit_type=self.unit_type,
                crs=self.crs,
            ),
        )
        self._snapshot = snapshot
        self.__dict__.clear()
        _remember(self, snapshot)
        return self

    def __getattr__(self, name: str) -> object:
        """Materialize an independent public field on access.

        Returns:
            The exported field or private attribute.

        Raises:
            AttributeError: If the attribute does not exist.
        """
        if name in {"collection", "unit_type", "crs", "features"}:
            snapshot = self._snapshot
            if snapshot is not None:
                if name in {"collection", "unit_type", "crs"}:
                    data = json.loads(snapshot.properties.schema.metadata[b"location"])
                else:
                    data = snapshot.to_dict()
                if name == "features":
                    return [Feature.model_validate(feature) for feature in data[name]]
                if name == "crs":
                    return CRS.model_validate(data[name])
                return data[name]
        private = object.__getattribute__(self, "__pydantic_private__")
        if private is not None and name in private:
            return private[name]
        raise AttributeError(name)

    @property
    def ids(self) -> list[str]:
        """Independent IDs in feature order."""
        if self._snapshot is not None and "features" not in self.__dict__:
            return self._snapshot.properties.column("id").to_pylist()
        return [feature.id for feature in self.features]

    def __eq__(self, other: object) -> bool:
        """Compare public contents.

        Returns:
            Whether both values have equal public contents.
        """
        return isinstance(other, Locations) and self.model_dump() == other.model_dump()

    @model_serializer
    def serialize(self, info: SerializationInfo) -> dict[str, Any]:
        """Export independently mutable public data.

        Returns:
            The requested public fields.
        """
        payload = LocationPayload.model_construct(
            **{name: getattr(self, name) for name in LocationPayload.model_fields}
        )
        return payload.model_dump(
            mode=info.mode,
            include=info.include,
            exclude=info.exclude,
            by_alias=info.by_alias,
            exclude_none=info.exclude_none,
            exclude_unset=info.exclude_unset,
            exclude_defaults=info.exclude_defaults,
        )
