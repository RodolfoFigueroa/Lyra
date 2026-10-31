"""Internal Arrow geometry buffers, independent of locations and storage.

All rows have four list levels: parts, rings, positions, ordinates. Types
with fewer levels use singleton wrappers. Variable position offsets preserve
arbitrary dimensions, including different dimensions within one geometry.
Arrow inputs may alias writable memory; this codec does not establish ownership.
"""

from collections.abc import Iterable, Iterator
from typing import Annotated, Any

import numpy as np
import pyarrow as pa
from lyra.sdk.models.geometry import (
    LineStringGeometry,
    MultiLineStringGeometry,
    MultiPointGeometry,
    MultiPolygonGeometry,
    PointGeometry,
    PolygonGeometry,
)
from numpy.typing import NDArray
from pydantic import Field, TypeAdapter

Geometry = (
    PointGeometry
    | MultiPointGeometry
    | LineStringGeometry
    | MultiLineStringGeometry
    | PolygonGeometry
    | MultiPolygonGeometry
)
_ADAPTER = TypeAdapter(Annotated[Geometry, Field(discriminator="type")])
_NAMES = (
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
)
_DEPTH = (3, 2, 2, 1, 1, 0)
_COORDINATES = pa.float64()
for _ in range(4):
    _COORDINATES = pa.large_list(_COORDINATES)
SCHEMA = pa.schema([("type", pa.int8()), ("coordinates", _COORDINATES)])


def encode(geometries: Iterable[Geometry | dict[str, Any]]) -> pa.Table:
    """Validate geometries and encode ordered coordinate buffers.

    Returns:
        An Arrow table with structural offsets and numeric geometry tags.
    """
    tags = []
    coordinates = []
    for geometry in geometries:
        raw = geometry.model_dump() if isinstance(geometry, Geometry) else geometry
        value = _ADAPTER.validate_python(raw)
        tag = _NAMES.index(value.type)
        nested = value.coordinates
        for _ in range(_DEPTH[tag]):
            nested = [nested]
        tags.append(tag)
        coordinates.append(nested)
    return pa.Table.from_arrays(
        [pa.array(tags, type=pa.int8()), pa.array(coordinates, type=_COORDINATES)],
        schema=SCHEMA,
    )


def coordinate_buffers(array: pa.Array) -> tuple[list[NDArray], NDArray]:
    """Return slice-relative offsets and referenced ordinates of validated columns."""
    offsets = []
    for _ in range(4):
        raw = array.offsets.to_numpy()
        start, end = int(raw[0]), int(raw[-1])
        offsets.append(raw - start)
        array = array.values.slice(start, end - start)
    return offsets, array.to_numpy()


def _validate_coordinates(array: pa.Array, tag: int) -> None:
    child = array
    for _ in range(5):
        if child.null_count:
            message = "Invalid null geometry coordinates"
            raise ValueError(message)
        if pa.types.is_large_list(child.type):
            start, end = int(child.offsets[0].as_py()), int(child.offsets[-1].as_py())
            child = child.values.slice(start, end - start)
    offsets, values = coordinate_buffers(array)
    lengths = [np.diff(level) for level in offsets]
    if any(np.any(lengths[level] != 1) for level in range(_DEPTH[tag])):
        message = "Invalid singleton geometry wrapper"
        raise ValueError(message)
    if np.any(lengths[3] < 2) or not np.all(np.isfinite(values)):
        message = "Invalid geometry position or nonfinite ordinate"
        raise ValueError(message)
    if tag in {2, 3} and np.any(lengths[2] < 2):
        message = "Invalid line length"
        raise ValueError(message)
    if tag in {4, 5}:
        _validate_rings(offsets, lengths, values)


def _validate_rings(
    offsets: list[NDArray], lengths: list[NDArray], values: NDArray
) -> None:
    if np.any(lengths[1] < 1) or np.any(lengths[2] < 4):
        message = "Invalid polygon or ring length"
        raise ValueError(message)
    for first, end in zip(offsets[2][:-1], offsets[2][1:], strict=True):
        left = values[offsets[3][first] : offsets[3][first + 1]]
        right = values[offsets[3][end - 1] : offsets[3][end]]
        if not np.array_equal(left, right):
            message = "Invalid polygon: rings must be closed"
            raise ValueError(message)


def validate(table: pa.Table) -> None:
    """Validate untrusted geometry columns without materializing Python geometry."""
    table.validate(full=True)
    validate_owned(table)


def validate_owned(table: pa.Table) -> None:
    """Check internal owned geometry after full Arrow structural validation.

    Raises:
        ValueError: If schema, tags, nesting, or coordinates are invalid.
    """
    if table.schema != SCHEMA:
        message = "Invalid geometry buffer schema"
        raise ValueError(message)
    for batch in table.to_batches(max_chunksize=4096):
        tags = batch.column(0)
        if tags.null_count:
            message = "Invalid geometry tag"
            raise ValueError(message)
        values = tags.to_numpy()
        if np.any((values < 0) | (values >= len(_NAMES))):
            message = "Invalid geometry tag"
            raise ValueError(message)
        for tag in range(len(_NAMES)):
            indices = np.flatnonzero(values == tag)
            if len(indices):
                _validate_coordinates(batch.column(1).take(indices), tag)


def geometry_rows(table: pa.Table) -> Iterator[dict[str, Any]]:
    """Materialize fresh dictionaries from already validated geometry columns.

    Yields:
        Detached geometry dictionaries in row order.
    """
    for batch in table.to_batches(max_chunksize=4096):
        for tag, nested in zip(
            batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True
        ):
            coordinates = nested
            for _ in range(_DEPTH[tag]):
                coordinates = coordinates[0]
            yield {"type": _NAMES[tag], "coordinates": coordinates}


def geometry_model(raw: dict[str, Any]) -> Geometry:
    """Construct a fresh model from an already validated, detached geometry row.

    Returns:
        The concrete geometry model owning the supplied detached coordinates.
    """
    classes = (
        PointGeometry,
        MultiPointGeometry,
        LineStringGeometry,
        MultiLineStringGeometry,
        PolygonGeometry,
        MultiPolygonGeometry,
    )
    return classes[_NAMES.index(raw["type"])].model_construct(**raw)


def decode(table: pa.Table) -> list[Geometry]:
    """Validate arbitrary columns and return independent geometry models.

    Returns:
        Fresh models in row order.
    """
    validate(table)
    return [geometry_model(row) for row in geometry_rows(table)]
