"""Library conversions between owned validated frames and typed relational cells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pyarrow as pa

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


@dataclass(frozen=True)
class StorageDescription(RowLayout):
    """Immutable declared layout; scalar dtypes are canonical reconstruction types."""

    properties: pa.Schema
    sqlite_types: tuple[str, ...]
    nullable: tuple[bool, ...]
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
                pd.Series(values, dtype=dtype).array.to_numpy(
                    dtype=object, na_value=None
                )
            )
    if not columns:
        return [()] * len(frame)
    return list(zip(*columns, strict=True))


def frame_from_rows(
    rows: Iterable[tuple[Cell, ...]], description: StorageDescription
) -> pd.DataFrame:
    """Reconstruct fresh native columns from stored logical types and declared CRS.

    Returns:
        Independent DataFrame or GeoDataFrame, including typed empty columns.

    Raises:
        ValueError: If a retained float column contains infinity.
    """
    records = list(rows)
    data = {}
    for index, (name, dtype) in enumerate(
        zip(description.columns, description.pandas_dtypes, strict=True)
    ):
        values = [row[index] for row in records]
        data[name] = (
            gpd.GeoSeries.from_wkb(
                np.asarray(values, dtype=object), crs=description.crs
            )
            if dtype == "geometry"
            else pd.Series(values, dtype=dtype)
        )
        if dtype == "Float64" and np.isinf(data[name]).any():
            msg = f"Column {name!r} contains infinity"
            raise ValueError(msg)
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
    """
    row: dict[str, str | float | bool | None] = {}
    for name, dtype, value in zip(
        description.columns, description.pandas_dtypes, cells, strict=True
    ):
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
