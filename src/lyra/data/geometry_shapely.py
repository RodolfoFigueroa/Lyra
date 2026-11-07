"""Vectorized WKB conversion for supported XY and XYZ geometry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import shapely

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from lyra.data.typed_storage import Snapshot

SCHEMA = pa.schema([("wkb", pa.binary())])


def _check(geometries: NDArray) -> None:
    kinds = shapely.get_type_id(geometries)
    if np.any(~np.isin(kinds, [0, 1, 3, 4, 5, 6])) or np.any(shapely.has_m(geometries)):
        msg = "Unsupported or measured geometry"
        raise ValueError(msg)
    if np.any(shapely.is_empty(geometries) & np.isin(kinds, [0, 1, 3])):
        msg = "Empty singular geometry is unsupported"
        raise ValueError(msg)
    if np.any(shapely.is_empty(shapely.get_parts(geometries[kinds >= 4]))):
        msg = "Empty multipart geometry component is unsupported"
        raise ValueError(msg)
    coordinates = shapely.get_coordinates(geometries)
    if not np.all(np.isfinite(coordinates)):
        msg = "Invalid or nonfinite geometry coordinates"
        raise ValueError(msg)
    z = shapely.get_coordinates(geometries[shapely.has_z(geometries)], include_z=True)
    if not np.all(np.isfinite(z)):
        msg = "Invalid or nonfinite geometry coordinates"
        raise ValueError(msg)


def ingest_geometries(geometries: NDArray) -> pa.Table:
    """Return WKB columns for supported geometry.

    Returns:
        A binary Arrow column containing WKB.
    """
    _check(geometries)
    values = shapely.to_wkb(
        geometries, byte_order=1, output_dimension=3, flavor="iso", include_srid=False
    )
    return pa.Table.from_arrays([pa.array(values, type=pa.binary())], schema=SCHEMA)


def frame_geometries(snapshot: Snapshot) -> NDArray:
    """Decode a snapshot's WKB into independent Shapely geometries.

    Returns:
        An object array of immutable Shapely geometries.

    Raises:
        ValueError: If the supplied data is invalid.
    """
    if snapshot.geometry is None:
        msg = "Snapshot has no geometry"
        raise ValueError(msg)
    return shapely.from_wkb(snapshot.geometry.column(0).to_numpy())


def validate(table: pa.Table) -> None:
    """Validate a WKB column using the native reader.

    Raises:
        ValueError: If the supplied data is invalid.
    """
    table.validate(full=True)
    if table.schema != SCHEMA or table.column(0).null_count:
        msg = "Invalid geometry buffer schema"
        raise ValueError(msg)
    _check(shapely.from_wkb(table.column(0).to_numpy()))
