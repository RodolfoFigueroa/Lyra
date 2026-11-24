"""Schema-directed intervention JSON decoding without provider acquisition."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from lyra.data.location_frames import validate_frame, validate_record_columns
from lyra.data.relational import WKT_PRECISION, logical_dtype
from lyra.data.results import validate_serializable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lyra.contracts.interventions import AcceptedIntervention
    from lyra.contracts.outputs import OutputMetadata
    from lyra.contracts.types import JsonValue


def _float_cell(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number == value else None


def _integer_cell(value: JsonValue) -> int | None:
    if type(value) is str and re.fullmatch(r"[+-]?[0-9]+", value):
        number = int(value)
        if -(2**63) <= number < 2**63:
            return number
    return None


# Geometry stays textual until the complete column is passed to GeoSeries.from_wkt.
_JSON_DECODERS = {
    "string": lambda value: value if type(value) is str else None,
    "Int64": _integer_cell,
    "Float64": _float_cell,
    "boolean": lambda value: value if type(value) is bool else None,
    "geometry": lambda value: (
        value if type(value) is str and "\x00" not in value else None
    ),
}


def encode_additions(
    frame: gpd.GeoDataFrame, metadata: OutputMetadata
) -> list[dict[str, str | float | bool | None]]:
    """Validate native logical types against discovery before encoding any rows.

    Returns:
        Complete schema-directed JSON records in positional row order.

    Raises:
        ValueError: If the submitted frame differs from the provider declaration.
    """
    validate_frame(frame, "location_id")
    columns = tuple(column.name for column in metadata.columns)
    validate_record_columns(frame, "location_id", columns)
    if frame.active_geometry_name != metadata.geometry:
        msg = "Additions must use the declared active geometry"
        raise ValueError(msg)
    dtypes = tuple(logical_dtype(column.dtype) for column in metadata.columns)
    for column, dtype in zip(metadata.columns, dtypes, strict=True):
        values = frame[column.name]
        if not column.nullable and values.isna().any():
            msg = f"Column {column.name!r} is not nullable"
            raise ValueError(msg)
        if dtype == "geometry":
            if frame.crs != CRS.from_user_input(column.parameters.get("crs")):
                msg = "Additions must use the declared CRS"
                raise ValueError(msg)
        elif not _matches_dtype(values, column.dtype, nullable=column.nullable):
            msg = f"Column {column.name!r} requires {column.dtype}, got {values.dtype}"
            raise ValueError(msg)
    validate_serializable(frame)
    return encode_validated_additions(frame, metadata)


def encode_validated_additions(
    frame: gpd.GeoDataFrame, metadata: OutputMetadata
) -> list[dict[str, str | float | bool | None]]:
    """Encode records already accepted against the provider declaration.

    Returns:
        Schema-directed JSON records in input order.
    """
    columns = {}
    for column in metadata.columns:
        values = frame[column.name]
        dtype = logical_dtype(column.dtype)
        if dtype == "geometry":
            values = values.to_wkt(rounding_precision=WKT_PRECISION)
        elif dtype == "Int64":
            values = values.astype("string")
        cells = values.to_numpy(dtype=object, copy=True)
        cells[values.isna().to_numpy()] = None
        columns[column.name] = cells
    return [
        dict(zip(columns, row, strict=True))
        for row in zip(*columns.values(), strict=True)
    ]


def _matches_dtype(values: pd.Series, declared: str, *, nullable: bool) -> bool:
    if logical_dtype(declared) == "string":
        return isinstance(values.dtype, pd.StringDtype) or (
            pd.api.types.is_object_dtype(values.dtype)
            and len(values) > 0
            and all(isinstance(value, str) for value in values.dropna())
        )
    if declared == "bool" and nullable and isinstance(values.dtype, pd.BooleanDtype):
        return True
    if declared.lower() in {"int64", "float64"}:
        return pd.api.types.is_dtype_equal(
            getattr(values.dtype, "numpy_dtype", values.dtype), declared.lower()
        )
    return pd.api.types.is_dtype_equal(values.dtype, declared)


def decode_additions(
    records: Sequence[Mapping[str, JsonValue]], metadata: OutputMetadata
) -> gpd.GeoDataFrame:
    """Reconstruct complete records, including empty and all-null typed columns.

    Returns:
        A fresh frame in the declared CRS; no executable checks are inferred.

    Raises:
        ValueError: If fields or the spatial declaration are incomplete.
    """
    names = {column.name for column in metadata.columns}
    if any(row.keys() != names for row in records):
        msg = "Addition records must contain exactly every declared column"
        raise ValueError(msg)
    geometry = next(
        (column for column in metadata.columns if column.name == metadata.geometry),
        None,
    )
    if geometry is None or not geometry.parameters.get("crs"):
        msg = "Additions require declared geometry and CRS"
        raise ValueError(msg)
    crs = cast("str", geometry.parameters["crs"])
    columns = {}
    for column in metadata.columns:
        decode = _JSON_DECODERS[logical_dtype(column.dtype)]
        values = []
        for row in records:
            cell = row[column.name]
            decoded = decode(cell)
            if decoded is None and (cell is not None or not column.nullable):
                msg = f"Invalid {column.dtype} JSON cell: {cell!r}"
                raise ValueError(msg)
            values.append(decoded)
        if column.dtype == "geometry":
            try:
                columns[column.name] = gpd.GeoSeries.from_wkt(
                    np.asarray(values, dtype=object), crs=crs
                )
            except shapely.errors.GEOSException as exc:
                msg = "Invalid geometry WKT"
                raise ValueError(msg) from exc
        else:
            dtype = column.dtype
            if any(value is None for value in values):
                dtype = {"int64": "Int64", "bool": "boolean"}.get(dtype, dtype)
            columns[column.name] = pd.Series(values, dtype=dtype)
    return gpd.GeoDataFrame(columns, geometry=metadata.geometry, crs=crs)


def accepted_additions(value: AcceptedIntervention) -> gpd.GeoDataFrame:
    """Return independent accepted records without the live registration."""
    return decode_additions(value.add, value.addition_schema)
