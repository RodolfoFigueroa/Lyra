"""Internal class boundary for Lyra's managed provider implementations."""

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyproj
import shapely

from lyra.sdk.locations import LocationProvider
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from lyra.sdk.resources import ConfigT


class ManagedLocationProvider(LocationProvider[ConfigT]):
    """Identify built-in query adapters by inheritance, without probing resources.

    This internal base supplies no acquisition implementation or public adapter
    protocol. Managed implementations own both exact-ID and query acquisition.
    """

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Materialize a validated filter using the captured record schema.

        Raises:
            NotImplementedError: Until the concrete managed adapter supplies execution.
        """
        raise NotImplementedError


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
