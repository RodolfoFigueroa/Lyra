"""Library conversions between owned validated frames and typed relational cells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pyarrow as pa

    from lyra.contracts.outputs import OutputMetadata


@dataclass(frozen=True)
class StorageDescription:
    """Immutable declared layout; scalar dtypes are canonical reconstruction types."""

    properties: pa.Schema
    columns: tuple[str, ...]
    pandas_dtypes: tuple[str, ...]
    sqlite_types: tuple[str, ...]
    nullable: tuple[bool, ...]
    crs: str | None
    geometry: str | None
    descriptions: tuple[str | None, ...]
    units: tuple[str | None, ...]
    metadata: OutputMetadata | None = None


Cell: TypeAlias = str | int | float | bool | bytes | None


def rows_from_validated(
    frame: pd.DataFrame, description: StorageDescription
) -> list[tuple[Cell, ...]]:
    """Convert a private producer-validated frame using its compiled description.

    This internal writer boundary requires runtime ownership and matching layout.
    External callers must first use storage_schema.prepare_native. Custom checks
    and coercion belong to that producer boundary, never this conversion.

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
    frame = pd.DataFrame(data, index=pd.RangeIndex(len(records)))
    if description.geometry is not None:
        return gpd.GeoDataFrame(
            frame, geometry=description.geometry, crs=description.crs
        )
    return frame
