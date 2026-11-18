"""Internal class boundary for Lyra's managed provider implementations."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyproj
import shapely

from lyra.data.intervention_records import decode_cell
from lyra.sdk.locations import LocationProvider
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import ProviderQuery, compile_query_schema
from lyra.sdk.resources import ConfigT

if TYPE_CHECKING:
    from lyra.contracts.interventions import AcceptedIntervention


class ManagedLocationProvider(LocationProvider[ConfigT]):
    """Identify built-in query adapters by inheritance, without probing resources.

    Managed implementations own both exact-ID and query acquisition.
    """

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Materialize a validated filter using the captured record schema.

        Raises:
            NotImplementedError: Until the concrete managed adapter supplies execution.
        """
        raise NotImplementedError

    def select_effective(
        self,
        expression: ir.Table,
        schema: ProviderSchema,
        intervention: "AcceptedIntervention",
    ) -> gpd.GeoDataFrame:
        """Select using the accepted changes for this execution's catalog.

        Raises:
            NotImplementedError: If the adapter cannot select effective records.
        """
        raise NotImplementedError


@dataclass
class StagedCatalog:
    """An execution-owned relation and the cleanup for its temporary objects."""

    relation: ir.Table | None = None
    drops: list[Callable[[], object]] = field(default_factory=list)

    def close(self) -> None:
        """Drop all reserved objects in reverse order, preserving the first failure."""
        self.relation = None
        drops = reversed(self.drops)
        self.drops = []
        try:
            for drop in drops:
                drop()
        finally:
            # If a drop failed, finish the rest without replacing its error.
            for drop in drops:
                with suppress(BaseException):
                    drop()


def staging_tables(
    intervention: "AcceptedIntervention", schema: ProviderSchema, query: ProviderQuery
) -> tuple[pa.Table, pa.Table]:
    """Return typed additions (including WKB) and removal IDs for managed staging."""
    columns = {
        column.name: [
            decode_cell(row[column.name], column.dtype, nullable=column.nullable)
            for row in intervention.add
        ]
        for column in schema.metadata.columns
    }
    return (
        pa.Table.from_pydict(columns, schema=query.interchange),
        pa.Table.from_pydict(
            {"location_id": intervention.remove},
            schema=pa.schema([query.interchange.field("location_id")]),
        ),
    )


def decode_records(data: pa.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
    """Reconstruct native columns without coercing IDs or validating records.

    Returns:
        Declared native columns and decoded geometry for producer validation.

    Raises:
        TypeError: If geometry has no explicit binary interchange type.
        ValueError: If columns or geometry SRIDs differ from the declaration.
    """
    query = compile_query_schema(schema.schema)
    if data.column_names != query.interchange.names:
        message = "Managed acquisition must return every registered column"
        raise ValueError(message)
    geometry_type = data.schema.field("geometry").type
    if not (
        pa.types.is_binary(geometry_type) or pa.types.is_large_binary(geometry_type)
    ):
        message = "Managed acquisition must return explicitly typed binary geometry"
        raise TypeError(message)
    geometry = gpd.GeoSeries.from_wkb(data["geometry"].to_pylist(), crs=query.crs)
    srids = shapely.get_srid(geometry.to_numpy())
    expected_srid = pyproj.CRS(query.crs).to_epsg()
    if any(srid not in {-1, 0, expected_srid} for srid in srids):
        message = "Managed geometry SRID conflicts with the declared CRS"
        raise ValueError(message)
    if not data.num_rows:
        return schema.empty_frame()
    columns = {}
    for name, column in schema.schema.columns.items():
        if name == "geometry":
            continue
        values = data[name].to_pylist()
        dtype = column.dtype.type
        if name == "location_id":
            # Only an explicitly string-typed batch can use a string dtype:
            # malformed IDs must reach producer validation without coercion.
            if not (
                pa.types.is_string(data[name].type)
                or pa.types.is_large_string(data[name].type)
            ):
                dtype = "object"
        elif data[name].null_count:
            # Preserve nulls for the producer's nullability check, even when the
            # declared numpy dtype cannot represent them. Avoid float inference
            # for nullable integers: it can lose precision before reconstruction.
            dtype = {"int64": "Int64", "bool": "boolean"}.get(str(column.dtype), dtype)
        columns[name] = pd.Series(values, dtype=dtype)
    columns["geometry"] = geometry
    return gpd.GeoDataFrame(columns, geometry="geometry", crs=query.crs)[
        list(schema.schema.columns)
    ]
