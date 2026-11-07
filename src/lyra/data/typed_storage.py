"""Owned Arrow snapshots with typed properties and standard WKB geometry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import geopandas as gpd
import pyarrow as pa
import pyproj

from lyra.contracts.locations import LocationContractReference
from lyra.data import (
    arrow_conversion,
    content_fingerprints,
    geometry_buffers,
    geometry_shapely,
)
from lyra.data.arrow_ownership import own

if TYPE_CHECKING:
    import pandas as pd


_set_frozen = object.__setattr__


@dataclass(frozen=True, slots=True)
class Scope:
    """Structural roles and spatial scope of an internal snapshot."""

    collection: str
    unit_type: str
    crs: str
    id_column: str
    geometry_column: str
    columns: tuple[str, ...]
    metric: bool = False
    contract: LocationContractReference | None = None
    data_revision: str | None = None

    def __post_init__(self) -> None:
        """Validate concrete bindings against the spatial scope.

        Raises:
            ValueError: If bindings are inconsistent.
        """
        if self.crs:
            try:
                # Match the GeoArrow PROJJSON representation used when decoding.
                canonical = pyproj.CRS(pyproj.CRS(self.crs).to_json_dict()).to_string()
            except pyproj.exceptions.CRSError as error:
                msg = "Invalid location CRS"
                raise ValueError(msg) from error
            _set_frozen(self, "crs", canonical)
        if self.contract is not None and (
            not isinstance(self.contract, LocationContractReference)
            or self.metric
            or self.contract.collection != self.collection
            or self.contract.unit_type != self.unit_type
        ):
            message = "Contract must match location scope"
            raise ValueError(message)
        if self.data_revision is not None and (
            self.contract is None
            or not isinstance(self.data_revision, str)
            or not self.data_revision
        ):
            message = "Revision requires a concrete contract and nonempty string"
            raise ValueError(message)


@dataclass(frozen=True, slots=True, init=False)
class Snapshot:
    """Internal typed storage; not a public metric result wrapper."""

    arrow: pa.Table
    _owned_buffers: tuple[pa.Table, pa.Table | None]
    _fingerprints: tuple[str, str, str] | None
    table: pa.Table
    geometry: pa.Table | None
    scope: Scope | None

    def __init__(
        self,
        table: pa.Table,
        geometry: pa.Table | None = None,
        scope: Scope | None = None,
    ) -> None:
        """Own and validate incoming buffers."""
        self.validate_contents(table, geometry, scope)
        data = own(self.combine(table, geometry, scope))
        table, geometry = self.split(data, scope)
        _set_frozen(self, "arrow", data)
        _set_frozen(self, "_fingerprints", None)
        _set_frozen(self, "table", table)
        _set_frozen(self, "geometry", geometry)
        _set_frozen(self, "scope", scope)
        _set_frozen(self, "_owned_buffers", (table, geometry))

    @staticmethod
    def combine(
        table: pa.Table, geometry: pa.Table | None, scope: Scope | None
    ) -> pa.Table:
        """Assemble one standard GeoArrow table.

        Returns:
            A table view sharing the supplied column buffers.
        """
        if geometry is None or scope is None:
            return table
        metadata = {
            b"ARROW:extension:name": b"geoarrow.wkb",
            b"ARROW:extension:metadata": json.dumps(
                {"crs": pyproj.CRS(scope.crs).to_json_dict() if scope.crs else None}
            ).encode(),
        }
        field = pa.field(scope.geometry_column, pa.binary(), metadata=metadata)
        return table.append_column(field, geometry.column(0)).select(
            list(scope.columns)
        )

    @staticmethod
    def split(data: pa.Table, scope: Scope | None) -> tuple[pa.Table, pa.Table | None]:
        """Derive property and geometry views over one backing table.

        Returns:
            Property and geometry views sharing the supplied column buffers.
        """
        if scope is None:
            return data, None
        geometry = pa.Table.from_arrays(
            [data[scope.geometry_column]], schema=geometry_shapely.SCHEMA
        )
        return data.drop([scope.geometry_column]), geometry

    @staticmethod
    def validate_contents(
        table: pa.Table,
        geometry: pa.Table | None,
        scope: Scope | None,
        *,
        geometry_validated: bool = False,
    ) -> None:
        """Check native structure without copying or trusting incoming buffers.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        table.validate(full=True)
        if len(set(table.column_names)) != table.num_columns:
            message = "Duplicate columns"
            raise ValueError(message)
        for field, column in zip(table.schema, table.columns, strict=True):
            if not field.nullable and column.null_count:
                message = "Nulls in nonnullable column"
                raise ValueError(message)
        if (geometry is None) != (scope is None):
            message = "Geometry requires scope"
            raise ValueError(message)
        if geometry is not None and scope is not None:
            if not geometry_validated:
                geometry_buffers.validate(geometry)
            if geometry.num_rows != table.num_rows:
                message = "Geometry and property row counts must match"
                raise ValueError(message)
            if not isinstance(scope.columns, tuple):
                message = "Structural column order must be immutable"
                raise ValueError(message)
            if (
                any(
                    not isinstance(value, str) or not value.strip()
                    for value in (
                        *(
                            ()
                            if scope.metric
                            else (scope.collection, scope.unit_type, scope.id_column)
                        ),
                        *(() if scope.metric else (scope.crs,)),
                        scope.geometry_column,
                    )
                )
                or len(set(scope.columns)) != len(scope.columns)
                or tuple(
                    name for name in scope.columns if name != scope.geometry_column
                )
                != tuple(table.column_names)
                or scope.geometry_column not in scope.columns
            ):
                message = "Invalid location structural metadata"
                raise ValueError(message)
            Snapshot._validate_ids(scope, table)

    @staticmethod
    def _validate_ids(scope: Scope, table: pa.Table) -> None:
        if scope.metric:
            return
        if scope.id_column not in table.column_names:
            message = "Missing location identifier"
            raise ValueError(message)
        identifiers = table[scope.id_column]
        if identifiers.null_count or len(identifiers.unique()) != table.num_rows:
            message = "Location identifiers must be nonnull and unique"
            raise ValueError(message)

    @property
    def fingerprints(self) -> tuple[str, str, str]:
        """Lazily cached geometry, properties and combined identities.

        Returns:
            The converted, detached value.
        """
        if self._fingerprints is None:
            scope = self.scope
            values = (
                None
                if scope is None
                else (
                    scope.collection,
                    scope.unit_type,
                    scope.crs,
                    scope.id_column,
                    scope.geometry_column,
                    *scope.columns,
                    json.dumps(
                        None if scope.contract is None else scope.contract.model_dump(),
                        sort_keys=True,
                    ),
                )
            )
            result = content_fingerprints.typed_snapshot(
                self.table, self.geometry, values
            )
            _set_frozen(self, "_fingerprints", result)
            return result
        return (
            self._fingerprints if self._fingerprints is not None else self.fingerprints
        )

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        schema: pa.Schema,
        *,
        columns: tuple[str, ...] | None = None,
    ) -> Self:
        """Return owned native columns in the supplied structural order.

        Returns:
            The requested typed value.

        Raises:
            ValueError: If columns differ from the native schema.
        """
        if not isinstance(frame, gpd.GeoDataFrame) and set(frame.columns) != set(
            schema.names
        ):
            message = "Expected exactly the declared columns"
            raise ValueError(message)
        columns = tuple(frame.columns) if columns is None else columns
        table = arrow_conversion.to_arrow(frame, schema, columns=columns)
        if isinstance(frame, gpd.GeoDataFrame):
            scope = Scope(
                "",
                "",
                "" if frame.crs is None else str(frame.crs),
                "",
                str(frame.geometry.name),
                columns,
                metric=True,
            )
            return cls(
                table,
                geometry_shapely.ingest_geometries(frame.geometry.to_numpy()),
                scope,
            )
        return cls(table)

    @classmethod
    def from_locations(  # ruff: ignore[too-many-arguments] - explicit native layout and scope
        cls,
        frame: gpd.GeoDataFrame,
        schema: pa.Schema,
        *,
        collection: str,
        unit_type: str,
        columns: tuple[str, ...] | None = None,
        id_column: str = "id",
    ) -> Self:
        """Return owned columns and geometry with spatial scope.

        Returns:
            The requested typed value.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        columns = tuple(frame.columns) if columns is None else columns
        table = arrow_conversion.to_arrow(frame, schema, columns=columns)
        if frame.crs is None:
            message = "Location CRS is required"
            raise ValueError(message)
        scope = Scope(
            collection,
            unit_type,
            str(frame.crs),
            id_column,
            str(frame.geometry.name),
            columns,
        )
        return cls(
            table, geometry_shapely.ingest_geometries(frame.geometry.to_numpy()), scope
        )

    def _derive(self, table: pa.Table, geometry: pa.Table | None) -> Self:
        # Internal callers supply only owned buffers or views over owned buffers.
        result = object.__new__(type(self))
        _set_frozen(result, "_fingerprints", None)
        _set_frozen(result, "arrow", self.combine(table, geometry, self.scope))
        _set_frozen(result, "table", table)
        _set_frozen(result, "geometry", geometry)
        _set_frozen(result, "scope", self.scope)
        _set_frozen(result, "_owned_buffers", (table, geometry))
        return result

    def slice(self, offset: int, length: int | None = None) -> Self:
        """Return a contiguous subset sharing immutable backing.

        Returns:
            The requested typed value.
        """
        return self._derive(
            self.table.slice(offset, length),
            None if self.geometry is None else self.geometry.slice(offset, length),
        )

    def take(self, indices: pa.Array) -> Self:
        """Return an owned ordered row selection.

        Returns:
            The requested typed value.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        if indices.null_count or not pa.types.is_integer(indices.type):
            message = "Selection requires nonnull integer indices"
            raise ValueError(message)
        return self._derive(
            own(self.table.take(indices)),
            None if self.geometry is None else own(self.geometry.take(indices)),
        )

    def project(self, columns: list[str]) -> Self:
        """Return nonspatial columns in requested order.

        Returns:
            The requested typed value.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        if len(set(columns)) != len(columns):
            message = "Duplicate projection columns"
            raise ValueError(message)
        result = self._derive(self.table.select(columns), None)
        _set_frozen(result, "scope", None)
        return result

    def to_frame(self) -> pd.DataFrame:
        """Return an independent native frame.

        Returns:
            The requested typed value.
        """
        frame = arrow_conversion.from_arrow(self.table).copy(deep=True)
        if self.scope is not None and self.scope.metric:
            scope = self.scope
            frame[scope.geometry_column] = geometry_shapely.frame_geometries(self)
            return gpd.GeoDataFrame(
                frame.loc[:, list(scope.columns)],
                geometry=scope.geometry_column,
                crs=scope.crs or None,
            )
        return frame
