"""Ordered GeoDataFrame conversion for scoped location values."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import shapely
from lyra.sdk import geometry_buffers, location_snapshot, property_buffers
from lyra.sdk.scalars import normalize_property
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Polygon,
    mapping,
)
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    from lyra.sdk.models.location_payload import LocationPayload


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
    _validate_frame(frame, id_column)
    geometry_column = frame.active_geometry_name
    if geometry_column is None:
        msg = "Active geometry required"
        raise ValueError(msg)
    return _encode_frame(frame, collection, unit_type, id_column, geometry_column)


def _validate_frame(frame: gpd.GeoDataFrame, id_column: str) -> None:
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
    geometries = []
    properties = []
    for row in frame.itertuples(index=False, name=None):
        values = dict(zip(frame.columns, row, strict=True))
        identifier = values.pop(id_column)
        if type(identifier) is not str or not identifier:
            msg = "Feature id must be a nonempty string"
            raise ValueError(msg)
        geometry = values.pop(geometry_column)
        if not isinstance(geometry, BaseGeometry):
            msg = (
                f"Column {geometry_column!r} requires a geometry for ID {identifier!r}"
            )
            raise TypeError(msg)
        if geometry.has_m:
            msg = "Measured geometry coordinates are not supported"
            raise ValueError(msg)
        ids.append(identifier)
        geometries.append(
            {
                "type": "Point",
                "coordinates": shapely.get_coordinates(
                    geometry, include_z=geometry.has_z
                )[0].tolist(),
            }
            if geometry.geom_type == "Point" and not geometry.is_empty
            else mapping(geometry)
        )
        properties.append(
            {
                column: normalize_property(value, column)
                for column, value in values.items()
                if value is not pd.NA or frame[column].dtype != object
            }
        )
    return location_snapshot.LocationSnapshot(
        geometry_buffers.encode(geometries),
        property_buffers.encode(
            ids,
            properties,
            collection=collection,
            unit_type=unit_type,
            crs={"type": "name", "properties": {"name": frame.crs.to_string()}},
        ),
    )


def locations_frame(
    locations: LocationPayload, *, id_column: str, geometry_column: str
) -> gpd.GeoDataFrame:
    """Reconstruct an independent frame without inferring property dtypes.

    Returns:
        Ordered rows with their original CRS.

    Raises:
        ValueError: If property names collide with identity or geometry columns.
    """
    _column_names(id_column, geometry_column)
    snapshot = getattr(locations, "_snapshot", None)
    if snapshot is None:
        snapshot = location_snapshot.LocationSnapshot(
            geometry_buffers.encode(feature.geometry for feature in locations.features),
            property_buffers.encode(
                locations.ids,
                (feature.properties for feature in locations.features),
                collection=locations.collection,
                unit_type=locations.unit_type,
                crs=locations.crs,
            ),
        )
    data = property_buffers.decode(snapshot.properties)
    rows = data["properties"]
    properties = dict.fromkeys(column for row in rows for column in row)
    for column in properties:
        if column in {id_column, geometry_column}:
            msg = f"Property column {column!r} collides with ID or geometry column"
            raise ValueError(msg)
    columns = {id_column: pd.Series(data["ids"], dtype=object)}
    columns.update(
        {
            column: pd.Series([row.get(column, pd.NA) for row in rows], dtype=object)
            for column in properties
        }
    )
    frame = pd.DataFrame(columns)
    frame[geometry_column] = gpd.GeoSeries(
        _frame_geometries(snapshot), crs=data["crs"]["properties"]["name"]
    )
    return gpd.GeoDataFrame(frame, geometry=geometry_column)


def _frame_geometries(
    snapshot: location_snapshot.LocationSnapshot,
) -> list[BaseGeometry]:
    geometries = []
    depths = (3, 2, 2, 1, 1, 0)
    for tag, nested in zip(
        snapshot.geometry["type"].to_pylist(),
        snapshot.geometry["coordinates"].to_pylist(),
        strict=True,
    ):
        coordinates = nested
        dimensions = {
            len(position) for part in coordinates for ring in part for position in ring
        }
        if dimensions - {2, 3} or len(dimensions) > 1:
            msg = "Geometry coordinate dimensions cannot be represented by Shapely"
            raise ValueError(msg)
        for _ in range(depths[tag]):
            coordinates = coordinates[0]
        if tag == 0:
            geometry = shapely.points(coordinates)
        elif tag == 4:
            geometry = Polygon(coordinates[0], coordinates[1:])
        elif tag == 5:
            geometry = MultiPolygon([(part[0], part[1:]) for part in coordinates])
        else:
            geometry = {1: MultiPoint, 2: LineString, 3: MultiLineString}[tag](
                coordinates
            )
        # GEOS can discard empty multipart members. Verify exact coordinate structure.
        if json.dumps(mapping(geometry)["coordinates"]) != json.dumps(coordinates):
            msg = "Geometry coordinate structure cannot be represented by Shapely"
            raise ValueError(msg)
        geometries.append(geometry)
    return geometries
