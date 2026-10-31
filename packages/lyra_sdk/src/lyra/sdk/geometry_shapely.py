"""Bulk Shapely ingestion and exports for location geometry columns."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import shapely
from lyra.sdk.geometry_buffers import SCHEMA, coordinate_buffers

if TYPE_CHECKING:
    from lyra.sdk.location_snapshot import LocationSnapshot
    from lyra.sdk.typed_storage import Snapshot
    from numpy.typing import NDArray

_BATCH_ROWS = 4096
_TYPES = (
    shapely.GeometryType.POINT,
    shapely.GeometryType.MULTIPOINT,
    shapely.GeometryType.LINESTRING,
    shapely.GeometryType.MULTILINESTRING,
    shapely.GeometryType.POLYGON,
    shapely.GeometryType.MULTIPOLYGON,
)


def _dimensions(offsets: list[NDArray]) -> NDArray:
    # Compose parent offsets to locate each row in the position-width array.
    starts = offsets[2][offsets[1][offsets[0]]]
    widths = np.diff(offsets[3])
    counts = np.diff(starts)
    dimensions = np.zeros(len(counts), dtype=np.int64)
    nonempty = counts != 0
    # Prefix counts avoid reduceat's special handling of repeated empty offsets.
    for width in (2, 3):
        matches = np.concatenate(([0], np.cumsum(widths == width)))
        dimensions[nonempty & (np.diff(matches[starts]) == counts)] = width
    if np.any(nonempty & (dimensions == 0)):
        msg = "Geometry coordinate dimensions cannot be represented by Shapely"
        raise ValueError(msg)
    return dimensions


def _construct(array: pa.Array, tag: int, dimension: int) -> NDArray:
    offsets, values = coordinate_buffers(array)
    coordinates = values.reshape(-1, dimension)
    selected = {
        0: (),
        1: (offsets[2],),
        2: (offsets[2],),
        3: (offsets[2], offsets[1]),
        4: (offsets[2], offsets[1]),
        5: (offsets[2], offsets[1], offsets[0]),
    }[tag]
    geometries = shapely.from_ragged_array(_TYPES[tag], coordinates, selected)
    kind, restored, restored_offsets = shapely.to_ragged_array(
        geometries, include_z=dimension == 3, include_m=False
    )
    if (
        kind != _TYPES[tag]
        or restored.shape != coordinates.shape
        or not np.array_equal(restored.view(np.uint64), coordinates.view(np.uint64))
        or len(selected) != len(restored_offsets)
        or any(
            not np.array_equal(left, right)
            for left, right in zip(selected, restored_offsets, strict=True)
        )
    ):
        msg = "Geometry coordinate structure cannot be represented by Shapely"
        raise ValueError(msg)
    return geometries


def frame_geometries(snapshot: LocationSnapshot | Snapshot) -> NDArray:
    """Return fresh geometries in snapshot order using bounded row batches.

    Returns:
        An independent object array, preserving exact coordinate structure.

    Raises:
        ValueError: If geometry is absent.
    """
    if snapshot.geometry is None:
        message = "Snapshot has no geometry"
        raise ValueError(message)
    result = np.empty(snapshot.geometry.num_rows, dtype=object)
    start = 0
    for batch in snapshot.geometry.to_batches(max_chunksize=_BATCH_ROWS):
        tags = batch.column(0).to_numpy()
        coordinates = batch.column(1)
        offsets, _ = coordinate_buffers(coordinates)
        dimensions = _dimensions(offsets)
        for tag, kind in enumerate(_TYPES):
            empty = np.flatnonzero((tags == tag) & (dimensions == 0))
            if len(empty):
                result[start + empty] = shapely.empty(len(empty), geom_type=kind)
            for dimension in (2, 3):
                indices = np.flatnonzero((tags == tag) & (dimensions == dimension))
                if not len(indices):
                    continue
                group = (
                    coordinates
                    if len(indices) == batch.num_rows
                    else coordinates.take(indices)
                )
                result[start + indices] = _construct(group, tag, dimension)
        start += batch.num_rows
    return result


def _ingest_group(geometries: NDArray, tag: int, dimension: int) -> pa.Array:
    kind, coordinates, offsets = shapely.to_ragged_array(
        geometries, include_z=dimension == 3, include_m=False
    )
    if kind != _TYPES[tag] or not np.all(np.isfinite(coordinates)):
        msg = "Invalid or nonfinite geometry coordinates"
        raise ValueError(msg)
    restored = shapely.from_ragged_array(kind, coordinates, offsets)
    if not np.array_equal(
        shapely.to_wkb(
            geometries, byte_order=1, output_dimension=3, include_srid=False
        ),
        shapely.to_wkb(restored, byte_order=1, output_dimension=3, include_srid=False),
    ):
        msg = "Geometry coordinate structure cannot be represented without loss"
        raise ValueError(msg)
    array = pa.LargeListArray.from_arrays(
        pa.array(np.arange(len(coordinates) + 1, dtype=np.int64) * dimension),
        pa.array(coordinates.reshape(-1), type=pa.float64()),
    )
    for offset in offsets:
        array = pa.LargeListArray.from_arrays(pa.array(offset, type=pa.int64()), array)
    for _ in range((3, 2, 2, 1, 1, 0)[tag]):
        array = pa.LargeListArray.from_arrays(
            pa.array(np.arange(len(array) + 1, dtype=np.int64)), array
        )
    return array


def _ingest_batch(geometries: NDArray) -> pa.Table:
    kinds = shapely.get_type_id(geometries)
    if np.any(~np.isin(kinds, _TYPES)) or np.any(shapely.has_m(geometries)):
        msg = "Unsupported or measured geometry"
        raise ValueError(msg)
    empty = shapely.is_empty(geometries)
    if np.any(empty & np.isin(kinds, [_TYPES[i] for i in (0, 2, 4)])):
        msg = "Empty singular geometry is unsupported"
        raise ValueError(msg)
    if np.any(empty & (shapely.get_num_geometries(geometries) != 0)):
        msg = "Empty geometry components cannot be represented without loss"
        raise ValueError(msg)
    dimensions = np.where(shapely.has_z(geometries), 3, 2)
    groups = []
    order = []
    for tag, kind in enumerate(_TYPES):
        indices = np.flatnonzero((kinds == kind) & empty)
        if len(indices):
            # Empty multipart geometries have no coordinate dimension to store.
            coordinates = pa.array(
                [([[[]]] if tag == 1 else [[]] if tag == 3 else []) for _ in indices],
                type=SCHEMA.field("coordinates").type,
            )
            groups.append(
                pa.Table.from_arrays(
                    [pa.array(np.full(len(indices), tag), type=pa.int8()), coordinates],
                    schema=SCHEMA,
                )
            )
            order.extend(indices)
        for dimension in (2, 3):
            indices = np.flatnonzero(
                (kinds == kind) & ~empty & (dimensions == dimension)
            )
            if not len(indices):
                continue
            coordinates = _ingest_group(geometries[indices], tag, dimension)
            groups.append(
                pa.Table.from_arrays(
                    [pa.array(np.full(len(indices), tag), type=pa.int8()), coordinates],
                    schema=SCHEMA,
                )
            )
            order.extend(indices)
    if len(groups) == 1:
        return groups[0]
    return pa.concat_tables(groups).take(np.argsort(order))


def ingest_geometries(geometries: NDArray) -> pa.Table:
    """Encode Shapely geometry in bounded batches without Python coordinates.

    Returns:
        Geometry columns in input row order, awaiting snapshot ownership.
    """
    if not len(geometries):
        return pa.Table.from_batches([], schema=SCHEMA)
    return pa.concat_tables(
        [
            _ingest_batch(geometries[start : start + _BATCH_ROWS])
            for start in range(0, len(geometries), _BATCH_ROWS)
        ]
    )
