"""Library conversions between owned validated frames and typed relational cells."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, TypeAlias, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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


_VALID_CELL: dict[str, Callable[[Cell], bool]] = {
    "string": lambda v: type(v) is str,
    "Int64": lambda v: type(v) is int and -(2**63) <= v < 2**63,
    "boolean": lambda v: type(v) is int and v in {0, 1},
    "Float64": lambda v: type(v) is float and math.isfinite(v),
    "geometry": lambda v: isinstance(v, bytes) and v[:1] in {b"\x00", b"\x01"},
}


def checked_column(
    rows: Iterable[tuple[Cell, ...]], index: int, layout: RowLayout
) -> list[Cell]:
    """Collect SQL cells in one pass, checking their physical representation.

    Returns:
        Values suitable for conversion to the declared dtype.

    Raises:
        ValueError: If a cell violates its declared type or nullability.
    """
    dtype = layout.pandas_dtypes[index]
    valid = _VALID_CELL[dtype]
    nullable = layout.nullable[index]
    values = []
    for row in rows:
        value = row[index]
        if not (nullable if value is None else valid(value)):
            msg = f"Invalid stored {dtype} in column {layout.columns[index]!r}"
            raise ValueError(msg)
        values.append(value)
    return values


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
        values = checked_column(records, index, description)
        data[name] = (
            gpd.GeoSeries(
                shapely.from_wkb(np.asarray(values, dtype=object)), crs=description.crs
            )
            if dtype == "geometry"
            else pd.Series(values, dtype=dtype)
        )
    frame = pd.DataFrame(data, index=pd.RangeIndex(len(records)))
    if description.geometry is not None:
        return gpd.GeoDataFrame(
            frame, geometry=description.geometry, crs=description.crs
        )
    return frame


def geometry_wkt(geometry: shapely.Geometry) -> str:
    """Return round-trippable WKT for registered geometry and spatial literals."""
    # GEOS uses fixed notation down to 1e-4. At that magnitude 20 decimal
    # places retain 17 significant digits, enough to round-trip a binary64.
    return shapely.to_wkt(geometry, rounding_precision=20)


def encode_row(
    cells: tuple[Cell, ...], description: RowLayout
) -> dict[str, str | float | bool | None]:
    """Encode declared SQL cells without rounding, reprojection, or type inference.

    Returns:
        Every declared column mapped to its fixed JSON cell representation.

    Raises:
        ValueError: If a cell violates its declared representation.
    """
    row: dict[str, str | float | bool | None] = {}
    for name, dtype, nullable, value in zip(
        description.columns,
        description.pandas_dtypes,
        description.nullable,
        cells,
        strict=True,
    ):
        if not (nullable if value is None else _VALID_CELL[dtype](value)):
            msg = f"Invalid stored {dtype} in column {name!r}"
            raise ValueError(msg)
        if value is None:
            row[name] = None
        elif dtype == "geometry":
            row[name] = geometry_wkt(shapely.from_wkb(cast("bytes", value)))
        elif dtype == "boolean":
            row[name] = bool(value)
        elif dtype == "Float64":
            row[name] = float(value)
        else:
            # Both declared strings and signed integers have exact string cells.
            row[name] = str(value)
    return row
