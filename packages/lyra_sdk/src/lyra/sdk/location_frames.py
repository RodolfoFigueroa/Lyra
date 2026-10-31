"""Ordered GeoDataFrame conversion for scoped location values."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from lyra.sdk import location_snapshot, property_buffers
from lyra.sdk.geometry_shapely import ingest_geometries
from lyra.sdk.scalars import normalize_property


def _column_names(id_column: str, geometry_column: str) -> None:
    if not isinstance(id_column, str) or not isinstance(geometry_column, str):
        msg = "ID and geometry column names must be strings"
        raise TypeError(msg)
    if id_column == geometry_column:
        msg = "ID and geometry column names must be distinct"
        raise ValueError(msg)


def frame_snapshot(
    frame: gpd.GeoDataFrame, *, collection: str, unit_type: str, id_column: str
) -> location_snapshot.LocationSnapshot:
    """Build a location payload without retaining mutable frame data.

    Returns:
        An owned validated snapshot.

    Raises:
        ValueError: If the active geometry is missing.
    """
    validate_frame(frame, id_column)
    geometry_column = frame.active_geometry_name
    if geometry_column is None:
        msg = "Active geometry required"
        raise ValueError(msg)
    return _encode_frame(frame, collection, unit_type, id_column, geometry_column)


def validate_frame(frame: gpd.GeoDataFrame, id_column: str) -> None:
    """Check frame structure before reading or validating its contents.

    Raises:
        TypeError: If the input is not a GeoDataFrame.
        ValueError: If columns, identity or CRS metadata are missing or invalid.
    """
    if not isinstance(frame, gpd.GeoDataFrame):
        msg = "Locations require a GeoDataFrame"
        raise TypeError(msg)
    if not frame.columns.is_unique:
        msg = "GeoDataFrame column names must be unique"
        raise ValueError(msg)
    if any(not isinstance(column, str) for column in frame.columns):
        msg = "GeoDataFrame column names must be strings"
        raise ValueError(msg)
    geometry_column = frame.active_geometry_name
    if geometry_column is None:
        msg = "GeoDataFrame requires an active geometry column"
        raise ValueError(msg)
    _column_names(id_column, geometry_column)
    if id_column not in frame.columns:
        msg = f"Missing ID column {id_column!r}"
        raise ValueError(msg)
    if frame.crs is None:
        msg = "GeoDataFrame requires a known CRS"
        raise ValueError(msg)


def _encode_frame(
    frame: gpd.GeoDataFrame,
    collection: str,
    unit_type: str,
    id_column: str,
    geometry_column: str,
) -> location_snapshot.LocationSnapshot:
    if frame.crs is None:
        msg = "Known CRS required"
        raise ValueError(msg)
    ids = []
    properties = []
    for row in frame.itertuples(index=False, name=None):
        values = dict(zip(frame.columns, row, strict=True))
        identifier = values.pop(id_column)
        if type(identifier) is not str or not identifier:
            msg = "Feature id must be a nonempty string"
            raise ValueError(msg)
        values.pop(geometry_column)
        ids.append(identifier)
        properties.append(
            {
                column: normalize_property(value, column)
                for column, value in values.items()
                if value is not pd.NA or frame[column].dtype != object
            }
        )
    return location_snapshot.LocationSnapshot(
        ingest_geometries(frame.geometry.to_numpy()),
        property_buffers.encode(
            ids,
            properties,
            collection=collection,
            unit_type=unit_type,
            crs={"type": "name", "properties": {"name": frame.crs.to_string()}},
        ),
    )
