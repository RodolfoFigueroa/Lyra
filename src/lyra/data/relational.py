"""Library conversions between owned validated frames and typed relational cells."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, TypeAlias

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from lyra.contracts.outputs import OutputMetadata


_LOGICAL_DTYPES = {
    "str": "string",
    "string": "string",
    "string[pyarrow]": "string",
    "string[python]": "string",
    "int64": "Int64",
    "Int64": "Int64",
    "bool": "boolean",
    "boolean": "boolean",
    "float64": "Float64",
    "Float64": "Float64",
    "geometry": "geometry",
}


def logical_dtype(dtype: str) -> str:
    """Return the canonical pandas dtype for a declared relational column.

    Raises:
        ValueError: If the declared dtype has no supported cell encoding.
    """
    try:
        return _LOGICAL_DTYPES[dtype]
    except KeyError as exc:
        msg = f"Unsupported storage dtype: {dtype}"
        raise ValueError(msg) from exc


@dataclass(frozen=True)
class RowLayout:
    """Column names and logical pandas dtypes used by the row codecs."""

    columns: tuple[str, ...]
    pandas_dtypes: tuple[str, ...]
    nullable: tuple[bool, ...]


@dataclass(frozen=True)
class StorageDescription(RowLayout):
    """Immutable declared layout; scalar dtypes are canonical reconstruction types."""

    sqlite_types: tuple[str, ...]
    crs: str | None
    geometry: str | None
    descriptions: tuple[str | None, ...]
    units: tuple[str | None, ...]
    metadata: OutputMetadata | None = None


Cell: TypeAlias = str | int | float | bool | bytes | None


def rows_from_validated(
    frame: pd.DataFrame, description: RowLayout
) -> list[tuple[Cell, ...]]:
    """Convert an owned, validated frame using its declared row layout.

    Callers validate the frame against its declaration before conversion.

    Returns:
        Native SQL cells, with missing scalars and geometries represented by None.
    """
    columns = []
    for name, dtype in zip(description.columns, description.pandas_dtypes, strict=True):
        values = frame[name]
        if dtype == "geometry":
            columns.append(shapely.to_wkb(values.to_numpy()).tolist())
        else:
            # Extension-array conversion produces Python scalars without inferring
            # a logical type from populated, empty, or all-null data.
            columns.append(
                pd.Series(
                    values, dtype="Int64" if dtype == "boolean" else dtype
                ).array.to_numpy(dtype=object, na_value=None)
            )
    return list(zip(*columns, strict=True))


_WKB_UINT32 = (struct.Struct(">I"), struct.Struct("<I"))


def _wkb_member(blob: bytes, offset: int) -> tuple[int, int]:
    """Return the next byte offset and number of nested geometry headers.

    Raises:
        ValueError: If a header specifies an unknown byte order or type.
    """
    order = blob[offset]
    if order > 1:
        msg = "Invalid WKB byte order"
        raise ValueError(msg)
    uint32 = _WKB_UINT32[order]
    (type_code,) = uint32.unpack_from(blob, offset + 1)
    # EWKB encodes Z, M, and SRID as flags. ISO WKB uses type offsets for Z/M.
    dimensions, kind = divmod(type_code & 0x1FFFFFFF, 1000)
    if dimensions > 3 or kind not in range(1, 8):
        msg = "Invalid WKB geometry type"
        raise ValueError(msg)
    has_z = bool(type_code & 0x80000000 or dimensions in {1, 3})
    has_m = bool(type_code & 0x40000000 or dimensions in {2, 3})
    stride = 8 * (2 + has_z + has_m)
    offset += 5 + (4 if type_code & 0x20000000 else 0)
    if kind == 1:  # Point
        return offset + stride, 0
    (count,) = uint32.unpack_from(blob, offset)
    offset += 4
    if kind == 2:  # LineString
        return offset + count * stride, 0
    if kind == 3:  # Polygon
        for _ in range(count):
            (points,) = uint32.unpack_from(blob, offset)
            offset += 4 + points * stride
        return offset, 0
    return offset, count  # Multi-geometries and GeometryCollection


def binary_geometry(value: Cell) -> bytes:
    """Return a blob containing exactly one complete binary WKB geometry.

    Raises:
        TypeError: If the cell is not binary.
        ValueError: If the WKB is malformed, truncated, or has trailing bytes.
    """
    if not isinstance(value, bytes):
        msg = "Invalid binary WKB geometry"
        raise TypeError(msg)
    offset = 0
    pending = 1
    try:
        while pending:
            offset, children = _wkb_member(value, offset)
            pending += children - 1
    except (IndexError, struct.error) as error:
        msg = "Truncated binary WKB geometry"
        raise ValueError(msg) from error
    if offset != len(value):
        msg = "Invalid binary WKB geometry length"
        raise ValueError(msg)
    return value


def decode_geometries(values: Sequence[Cell]) -> np.ndarray:
    """Return decoded geometries from a bounded batch, preserving nulls."""
    binary = [None if value is None else binary_geometry(value) for value in values]
    return shapely.from_wkb(np.asarray(binary, dtype=object))


def frame_from_rows(
    rows: Iterable[tuple[Cell, ...]], description: StorageDescription
) -> pd.DataFrame:
    """Reconstruct fresh native columns from stored logical types and declared CRS.

    Returns:
        Independent DataFrame or GeoDataFrame, including typed empty columns.
    """
    iterator = iter(rows)
    frames = []
    while batch := list(islice(iterator, 1024)):
        frames.append(_frame_batch(batch, description))
    if not frames:
        return _frame_batch([], description)
    return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)


def _frame_batch(
    records: list[tuple[Cell, ...]], description: StorageDescription
) -> pd.DataFrame:
    data = {}
    for index, (name, dtype) in enumerate(
        zip(description.columns, description.pandas_dtypes, strict=True)
    ):
        values = [row[index] for row in records]
        data[name] = (
            gpd.GeoSeries(decode_geometries(values), crs=description.crs)
            if dtype == "geometry"
            else pd.Series(values, dtype=dtype)
        )
    frame = pd.DataFrame(data, index=pd.RangeIndex(len(records)))
    if description.geometry is not None:
        return gpd.GeoDataFrame(
            frame, geometry=description.geometry, crs=description.crs
        )
    return frame


WKT_PRECISION = 20


def geometry_wkt(geometry: shapely.Geometry) -> str:
    """Return round-trippable WKT for registered geometry and spatial literals."""
    # GEOS uses fixed notation down to 1e-4. At that magnitude 20 decimal
    # places retain 17 significant digits, enough to round-trip a binary64.
    return shapely.to_wkt(geometry, rounding_precision=WKT_PRECISION)


def encode_row(
    cells: tuple[Cell, ...], description: RowLayout
) -> dict[str, str | float | bool | None]:
    """Encode declared SQL cells without rounding, reprojection, or type inference.

    Returns:
        Every declared column mapped to its fixed JSON cell representation.
    """
    row: dict[str, str | float | bool | None] = {}
    for name, dtype, value in zip(
        description.columns,
        description.pandas_dtypes,
        cells,
        strict=True,
    ):
        if value is None:
            row[name] = None
        elif dtype == "geometry":
            row[name] = geometry_wkt(shapely.from_wkb(binary_geometry(value)))
        elif dtype == "boolean":
            row[name] = bool(value)
        elif dtype == "Float64":
            row[name] = float(value)
        else:
            # Both declared strings and signed integers have exact string cells.
            row[name] = str(value)
    return row
