"""Internal Arrow geometry buffers, independent of locations and storage.

All rows have four list levels: parts, rings, positions, ordinates. Types
with fewer levels use singleton wrappers. Variable position offsets preserve
arbitrary dimensions, including different dimensions within one geometry.
Arrow inputs may alias writable memory; this codec does not establish ownership.
"""

from collections.abc import Iterable
from typing import Annotated, Any

import pyarrow as pa
from lyra.sdk.models.geometry import (
    LineStringGeometry,
    MultiLineStringGeometry,
    MultiPointGeometry,
    MultiPolygonGeometry,
    PointGeometry,
    PolygonGeometry,
)
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


def decode(table: pa.Table) -> list[Geometry]:
    """Validate untrusted Arrow structure and return independent geometry models.

    Returns:
        Ordered validated geometry models, regardless of chunks or row slices.

    Raises:
        ValueError: If the schema, tags, wrappers, or geometry values are invalid.
    """
    if table.schema != SCHEMA:
        message = "Invalid geometry buffer schema"
        raise ValueError(message)
    table.validate(full=True)
    result = []
    for tag, nested in zip(
        table.column("type").to_pylist(),
        table.column("coordinates").to_pylist(),
        strict=True,
    ):
        if tag is None or not 0 <= tag < len(_NAMES):
            message = "Invalid geometry tag"
            raise ValueError(message)
        coordinates = nested
        for _ in range(_DEPTH[tag]):
            if not isinstance(coordinates, list) or len(coordinates) != 1:
                message = "Invalid singleton geometry wrapper"
                raise ValueError(message)
            coordinates = coordinates[0]
        result.append(
            _ADAPTER.validate_python({"type": _NAMES[tag], "coordinates": coordinates})
        )
    return result
