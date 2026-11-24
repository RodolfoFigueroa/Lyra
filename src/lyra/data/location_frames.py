"""Structural validation for producer location frames."""

from collections.abc import Iterable

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
        msg = "Location records require a GeoDataFrame"
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


def validate_record_columns(
    frame: gpd.GeoDataFrame, id_column: str, columns: Iterable[str]
) -> None:
    """Check the declared record shape and native IDs before encoding.

    Raises:
        ValueError: If the columns differ or an ID is invalid or repeated.
    """
    if not frame.columns.is_unique or set(frame.columns) != set(columns):
        msg = "Location frame must contain exactly the declared columns"
        raise ValueError(msg)
    validate_record_ids(frame, id_column, check_unique=True)


def validate_record_ids(
    frame: gpd.GeoDataFrame, id_column: str, *, check_unique: bool
) -> None:
    """Check opaque IDs and any uniqueness requirement outside Pandera.

    Raises:
        ValueError: If identifiers are missing, malformed or duplicated.
    """
    if list(frame.columns).count(id_column) != 1:
        msg = f"Expected one ID column {id_column!r}"
        raise ValueError(msg)
    identifiers = frame[id_column]
    if any(type(value) is not str or not value for value in identifiers):
        msg = "Feature id must be a nonempty string"
        raise ValueError(msg)
    if check_unique and not identifiers.is_unique:
        msg = "IDs must be unique within the scope"
        raise ValueError(msg)
