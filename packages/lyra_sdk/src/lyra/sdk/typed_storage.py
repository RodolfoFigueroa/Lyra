"""Private owned typed columns and aligned location geometry.

IPC is used only to detach buffers into immutable bytes, not as an artifact
protocol. Snapshots retain Arrow's declarative pandas metadata, never checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import geopandas as gpd
import pyarrow as pa
from lyra.sdk import (
    arrow_conversion,
    content_fingerprints,
    geometry_buffers,
    geometry_shapely,
)
from lyra.sdk.arrow_ownership import own

if TYPE_CHECKING:
    import pandas as pd
    import pandera.pandas as pandera


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


@dataclass(frozen=True, slots=True, init=False)
class Snapshot:
    """Internal typed storage; not a public metric result wrapper."""

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
        """Own and validate incoming buffers.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        table = own(table)
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
            geometry = own(geometry)
            geometry_buffers.validate_owned(geometry)
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
            self._validate_ids(scope, table)
        _set_frozen(self, "_fingerprints", None)
        _set_frozen(self, "table", table)
        _set_frozen(self, "geometry", geometry)
        _set_frozen(self, "scope", scope)

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
        """Lazily cached geometry, properties and combined identities."""
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
        cls, frame: pd.DataFrame, declaration: pandera.DataFrameSchema
    ) -> Self:
        """Return owned declared columns.

        Returns:
            The requested typed value.
        """
        table = arrow_conversion.to_arrow(frame, declaration)
        if isinstance(frame, gpd.GeoDataFrame):
            scope = Scope(
                "",
                "",
                "" if frame.crs is None else str(frame.crs),
                "",
                str(frame.geometry.name),
                tuple(declaration.columns),
                metric=True,
            )
            return cls(
                table,
                geometry_shapely.ingest_geometries(frame.geometry.to_numpy()),
                scope,
            )
        return cls(table)

    @classmethod
    def from_locations(
        cls,
        frame: gpd.GeoDataFrame,
        declaration: pandera.DataFrameSchema,
        *,
        collection: str,
        unit_type: str,
        id_column: str = "id",
    ) -> Self:
        """Return owned columns and geometry with spatial scope.

        Returns:
            The requested typed value.

        Raises:
            ValueError: If the supplied structure is invalid.
        """
        table = arrow_conversion.to_arrow(frame, declaration)
        if frame.crs is None:
            message = "Location CRS is required"
            raise ValueError(message)
        scope = Scope(
            collection,
            unit_type,
            str(frame.crs),
            id_column,
            str(frame.geometry.name),
            tuple(declaration.columns),
        )
        return cls(
            table, geometry_shapely.ingest_geometries(frame.geometry.to_numpy()), scope
        )

    def _derive(self, table: pa.Table, geometry: pa.Table | None) -> Self:
        result = object.__new__(type(self))
        _set_frozen(result, "_fingerprints", None)
        _set_frozen(result, "table", table)
        _set_frozen(result, "geometry", geometry)
        _set_frozen(result, "scope", self.scope)
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
