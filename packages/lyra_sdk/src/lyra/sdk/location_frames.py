"""Ordered GeoDataFrame conversion for scoped location values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import geopandas as gpd
import pandas as pd
from lyra.sdk.scalars import normalize_property
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    from lyra.sdk.models.location import Locations


def _column_names(id_column: str, geometry_column: str) -> None:
    if not isinstance(id_column, str) or not isinstance(geometry_column, str):
        msg = "ID and geometry column names must be strings"
        raise TypeError(msg)
    if id_column == geometry_column:
        msg = "ID and geometry column names must be distinct"
        raise ValueError(msg)


def frame_payload(
    frame: gpd.GeoDataFrame, *, collection: str, unit_type: str, id_column: str
) -> dict[str, Any]:
    """Build a location payload without retaining mutable frame data.

    Returns:
        A payload ready for location model validation.

    Raises:
        ValueError: If columns, geometry, properties, or CRS are invalid.
        TypeError: If the frame, column names, or geometry have invalid types.
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
    features = []
    for row in frame.itertuples(index=False, name=None):
        values = dict(zip(frame.columns, row, strict=True))
        identifier = values.pop(id_column)
        geometry = values.pop(geometry_column)
        if not isinstance(geometry, BaseGeometry):
            msg = (
                f"Column {geometry_column!r} requires a geometry for ID {identifier!r}"
            )
            raise TypeError(msg)
        features.append(
            {
                "type": "Feature",
                "id": identifier,
                "geometry": mapping(geometry),
                "properties": {
                    column: normalize_property(value, column)
                    for column, value in values.items()
                },
            }
        )
    return {
        "collection": collection,
        "unit_type": unit_type,
        "crs": {"type": "name", "properties": {"name": frame.crs.to_string()}},
        "features": features,
    }


def locations_frame(
    locations: Locations, *, id_column: str, geometry_column: str
) -> gpd.GeoDataFrame:
    """Reconstruct an independent frame without inferring property dtypes.

    Returns:
        Ordered rows with their original CRS.

    Raises:
        ValueError: If property names collide with identity or geometry columns.
    """
    _column_names(id_column, geometry_column)
    properties = dict.fromkeys(
        column for feature in locations.features for column in feature.properties
    )
    for column in properties:
        if column in {id_column, geometry_column}:
            msg = f"Property column {column!r} collides with ID or geometry column"
            raise ValueError(msg)
    columns = {id_column: pd.Series(locations.ids, dtype=object)}
    columns.update(
        {
            column: pd.Series(
                [feature.properties.get(column) for feature in locations.features],
                dtype=object,
            )
            for column in properties
        }
    )
    frame = pd.DataFrame(columns)
    frame[geometry_column] = gpd.GeoSeries(
        [shape(feature.geometry.model_dump()) for feature in locations.features],
        crs=locations.crs.properties.name,
    )
    return gpd.GeoDataFrame(frame, geometry=geometry_column)
