"""Structural validation for producer location frames."""

import geopandas as gpd


def _column_names(id_column: str, geometry_column: str) -> None:
    if not isinstance(id_column, str) or not isinstance(geometry_column, str):
        msg = "ID and geometry column names must be strings"
        raise TypeError(msg)
    if id_column == geometry_column:
        msg = "ID and geometry column names must be distinct"
        raise ValueError(msg)


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
