"""Immutable location values and lazy scalar views."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, final, overload

import geopandas as gpd
import pyarrow as pa
import pyarrow.compute as pc
from lyra.sdk import location_schema
from lyra.sdk.geometry_shapely import frame_geometries
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.geometry import CRS, Feature
from lyra.sdk.models.location_payload import (
    CollectionName,
    LocationSelection,
    UnitType,
)

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "CollectionName",
    "LocationSelection",
    "Locations",
    "UnitType",
]


Scalar = str | bool | int | float | date | datetime | None


@dataclass(frozen=True, slots=True, eq=False)
class _View(Sequence):
    __hash__ = None
    snapshot: LocationSnapshot
    column: int
    indices: range

    def __len__(self) -> int:
        return len(self.indices)

    @overload
    def __getitem__(self, index: int) -> Scalar: ...
    @overload
    def __getitem__(self, index: slice) -> _View: ...
    def __getitem__(self, index: int | slice) -> Scalar | _View:
        selected = self.indices[index]
        if isinstance(selected, range):
            return _View(self.snapshot, self.column, selected)
        return self.snapshot.table.column(self.column)[selected].as_py()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Sequence) and list(self) == list(other)


def validated_locations(value: object) -> Locations:
    """Require a complete runtime value at invocation boundaries.

    Returns:
        The complete immutable runtime value.

    Raises:
        TypeError: If the requested arguments are invalid.
    """
    if type(value) is not Locations or not isinstance(
        getattr(value, "_snapshot", None), LocationSnapshot
    ):
        message = "Expected validated Locations; construct with a location schema"
        raise TypeError(message)
    snapshot = _get_frozen(value, "_snapshot")
    if (
        not hasattr(snapshot, "table")
        or not hasattr(snapshot, "_fingerprints")
        or snapshot.scope is None
        or snapshot.geometry is None
    ):
        message = "Incomplete location snapshot"
        raise TypeError(message)
    return value


def location_snapshot(value: Locations) -> LocationSnapshot:
    """Return owned columns.

    Returns:
        The validated snapshot backing the value.
    """
    return _get_frozen(validated_locations(value), "_snapshot")


def location_fingerprint(value: Locations) -> str:
    """Return canonical content identity.

    Returns:
        The cached canonical content fingerprint.
    """
    return location_snapshot(value).fingerprint


def locations_from_snapshot(snapshot: LocationSnapshot) -> Locations:
    """Wrap a concrete initialized snapshot.

    Returns:
        A runtime value sharing the immutable snapshot.

    Raises:
        TypeError: If the requested arguments are invalid.
    """
    if (
        type(snapshot) is not LocationSnapshot
        or not hasattr(snapshot, "table")
        or not hasattr(snapshot, "_fingerprints")
        or snapshot.scope is None
        or snapshot.geometry is None
    ):
        message = "Expected an initialized LocationSnapshot"
        raise TypeError(message)
    value = object.__new__(Locations)
    _set_frozen(value, "_snapshot", snapshot)
    return value


def _count(value: int | None, *, name: str, minimum: int = 0) -> None:
    if value is not None and (type(value) is not int or value < minimum):
        message = f"{name} must be an integer >= {minimum}"
        raise ValueError(message)


@dataclass(frozen=True, slots=True, init=False)
class _Exports:
    _snapshot: LocationSnapshot

    def __len__(self) -> int:
        """Return the stored row count.

        Returns:
            The stored row count.
        """
        return self._snapshot.table.num_rows

    def iter_features(
        self, *, limit: int | None = None, batch_size: int = 256
    ) -> Iterator[Feature]:
        """Stream detached mutable features in bounded batches.

        Returns:
            An iterator decoding at most one batch per advance.

        Raises:
            ValueError: If the requested arguments are invalid.
        """
        _count(limit, name="limit")
        _count(batch_size, name="batch_size", minimum=1)
        if batch_size is None:
            message = "batch_size must be a positive integer"
            raise ValueError(message)
        count = len(self) if limit is None else min(limit, len(self))
        return self._batches(count, batch_size)

    def _batches(self, count: int, batch_size: int) -> Iterator[Feature]:
        for offset in range(0, count, batch_size):
            yield from self._snapshot.slice(
                offset, min(batch_size, count - offset)
            ).to_features()

    def to_features(self, *, limit: int | None = None) -> list[Feature]:
        """Materialize independent mutable feature models.

        Returns:
            A detached list of mutable feature models.
        """
        return list(self.iter_features(limit=limit))

    def to_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        """Export detached scoped data directly from buffers.

        Returns:
            Independent scope metadata and feature dictionaries.
        """
        return self._snapshot.to_dict(limit=limit)

    def to_json(self, *, limit: int | None = None) -> str:
        """Export scoped JSON data.

        Returns:
            The scoped payload encoded as JSON.
        """
        return json.dumps(self.to_dict(limit=limit))


@final
@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class Locations(_Exports):
    """Owned columnar locations with explicit, independent exports."""

    __hash__ = None

    def __new__(cls) -> Locations:
        """Require construction through a validated factory.

        Raises:
            TypeError: Always; use a factory.
        """
        message = "Use a Locations factory"
        raise TypeError(message)

    @classmethod
    def from_geodataframe(
        cls,
        frame: gpd.GeoDataFrame,
        *,
        collection: str,
        unit_type: str,
        schema: location_schema.LocationOutputSchema,
    ) -> Locations:
        """Validate and own producer columns using an explicit location contract.

        Returns:
            A validated value owning the frame contents.

        Raises:
            TypeError: If no compiled location contract is supplied.
        """
        if not isinstance(schema, location_schema.LocationOutputSchema):
            message = "schema must be a LocationOutputSchema"
            raise TypeError(message)
        return schema.build(frame, collection=collection, unit_type=unit_type)

    def _metadata(self) -> dict[str, Any]:
        return self._snapshot.metadata

    @property
    def collection(self) -> str:
        """Collection scope."""
        return self._metadata()["collection"]

    @property
    def unit_type(self) -> str:
        """Unit scope."""
        return self._metadata()["unit_type"]

    @property
    def crs(self) -> CRS:
        """Independent CRS metadata."""
        return CRS.model_validate(self._metadata()["crs"])

    def __len__(self) -> int:
        """Return the stored row count.

        Returns:
            The stored row count.
        """
        return self._snapshot.table.num_rows

    @property
    def is_empty(self) -> bool:
        """Whether the stored row count is zero."""
        return len(self) == 0

    @property
    def ids(self) -> Sequence[str]:
        """Lazy read-only IDs in stored order."""
        return _View(
            self._snapshot,
            self._snapshot.table.schema.get_field_index(self._snapshot.scope.id_column),
            range(len(self)),
        )

    @property
    def property_names(self) -> tuple[str, ...]:
        """Property names in metadata order."""
        return tuple(self._metadata()["keys"])

    def property(self, name: str) -> Sequence[Scalar]:
        """Read a lazy typed scalar column with logical nulls.

        Returns:
            A read-only view over the selected property column.

        Raises:
            KeyError: If the requested arguments are invalid.
        """
        if name not in self.property_names:
            raise KeyError(name)
        return _View(
            self._snapshot,
            self._snapshot.table.schema.get_field_index(name),
            range(len(self)),
        )

    def slice(self, offset: int, length: int | None = None) -> Locations:
        """Share a contiguous range of immutable buffers.

        Returns:
            A value sharing the selected contiguous buffer range.

        Raises:
            ValueError: If the requested arguments are invalid.
        """
        _count(offset, name="offset")
        if offset is None:
            message = "offset must be an integer"
            raise ValueError(message)
        _count(length, name="length")
        offset = min(offset, len(self))
        length = (
            len(self) - offset if length is None else min(length, len(self) - offset)
        )
        return locations_from_snapshot(self._snapshot.slice(offset, length))

    def select_ids(self, ids: Sequence[str]) -> Locations:
        """Select rows in requested order, rejecting duplicates and unknown IDs.

        Returns:
            A value owning selected rows in the requested order.

        Raises:
            KeyError: If the requested arguments are invalid.
            ValueError: If the requested arguments are invalid.
        """
        requested = list(ids)
        if any(type(value) is not str or not value for value in requested) or len(
            set(requested)
        ) != len(requested):
            message = "Selection IDs must be unique nonempty strings"
            raise ValueError(message)
        identifiers = self._snapshot.table[self._snapshot.scope.id_column]
        if pa.types.is_dictionary(identifiers.type):
            identifiers = pc.cast(identifiers, identifiers.type.value_type)
        indices = pc.call_function(
            "index_in",
            [pa.array(requested, type=identifiers.type)],
            options=pc.SetLookupOptions(identifiers),
        )
        if indices.null_count:
            raise KeyError(
                [
                    value
                    for value, index in zip(requested, indices.to_pylist(), strict=True)
                    if index is None
                ]
            )
        return locations_from_snapshot(self._snapshot.take(indices))

    def _columns(
        self, properties: Sequence[str] | None, structural: Sequence[str]
    ) -> tuple[str, ...]:
        names = self.property_names if properties is None else tuple(properties)
        if any(type(name) is not str or not name for name in structural) or len(
            set(structural)
        ) != len(structural):
            message = "Structural column names must be distinct nonempty strings"
            raise ValueError(message)
        if len(set(names)) != len(names) or set(names).intersection(structural):
            message = "Duplicate property or property collides with structural column"
            raise ValueError(message)
        for name in names:
            if name not in self.property_names:
                raise KeyError(name)
        return names

    def to_dataframe(
        self, *, properties: Sequence[str] | None = None, id_column: str | None = None
    ) -> pd.DataFrame:
        """Export selected scalar columns without accessing geometry.

        Returns:
            A fresh frame containing IDs and selected scalar properties.
        """
        scope = self._snapshot.scope
        id_column = scope.id_column if id_column is None else id_column
        names = self._columns(properties, (id_column,))
        selected = (
            list(self._snapshot.table.column_names)
            if properties is None
            else [scope.id_column, *names]
        )
        return (
            self._snapshot.project(selected)
            .to_frame()
            .rename(columns={scope.id_column: id_column})
        )

    def to_geodataframe(
        self,
        *,
        properties: Sequence[str] | None = None,
        id_column: str | None = None,
        geometry_column: str | None = None,
    ) -> gpd.GeoDataFrame:
        """Export projected properties and bulk geometry with a fresh index.

        Returns:
            A fresh frame with projected properties, geometry, and CRS.
        """
        scope = self._snapshot.scope
        id_column = scope.id_column if id_column is None else id_column
        geometry_column = (
            scope.geometry_column if geometry_column is None else geometry_column
        )
        names = self._columns(properties, (id_column, geometry_column))
        frame = self.to_dataframe(properties=properties, id_column=id_column)
        frame[geometry_column] = gpd.GeoSeries(
            frame_geometries(self._snapshot), crs=self.crs.properties.name
        )
        order = (
            [
                id_column
                if name == scope.id_column
                else geometry_column
                if name == scope.geometry_column
                else name
                for name in scope.columns
            ]
            if properties is None
            else [id_column, *names, geometry_column]
        )
        return gpd.GeoDataFrame(frame[order], geometry=geometry_column)

    def __eq__(self, other: object) -> bool:
        """Compare canonical content identities.

        Returns:
            Whether scope, CRS, and typed ordered contents match.
        """
        return isinstance(other, Locations) and location_fingerprint(
            self
        ) == location_fingerprint(other)

    def __repr__(self) -> str:
        """Describe scope and count without decoding rows.

        Returns:
            A compact metadata representation.
        """
        return (
            f"Locations(collection={self.collection!r}, "
            f"unit_type={self.unit_type!r}, count={len(self)})"
        )


_set_frozen = object.__setattr__

_get_frozen = object.__getattribute__
