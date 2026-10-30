"""Bulk Shapely exports from validated, owned location columns."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import shapely

if TYPE_CHECKING:
    import pyarrow as pa
    from lyra.sdk.location_snapshot import LocationSnapshot
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


def _buffers(array: pa.Array) -> tuple[list[NDArray], NDArray]:
    # Levels map rows -> parts -> rings -> positions -> ordinates.
    offsets = []
    for _ in range(4):
        raw = array.offsets.to_numpy()
        start, end = int(raw[0]), int(raw[-1])
        offsets.append(raw - start)
        array = array.values.slice(start, end - start)
    return offsets, array.to_numpy()


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
    offsets, values = _buffers(array)
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


def frame_geometries(snapshot: LocationSnapshot) -> NDArray:
    """Return fresh geometries in snapshot order using bounded row batches.

    Returns:
        An independent object array, preserving exact coordinate structure.
    """
    result = np.empty(snapshot.geometry.num_rows, dtype=object)
    start = 0
    for batch in snapshot.geometry.to_batches(max_chunksize=_BATCH_ROWS):
        tags = batch.column(0).to_numpy()
        coordinates = batch.column(1)
        offsets, _ = _buffers(coordinates)
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
