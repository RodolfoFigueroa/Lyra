"""Geometry model adapters over standard WKB storage."""

from collections.abc import Iterable, Iterator
from typing import Annotated, Any

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field, TypeAdapter
from shapely.geometry import mapping, shape

from lyra.contracts.geometry import (
    LineStringGeometry,
    MultiLineStringGeometry,
    MultiPointGeometry,
    MultiPolygonGeometry,
    PointGeometry,
    PolygonGeometry,
)
from lyra.data.geometry_shapely import SCHEMA, ingest_geometries, validate

__all__ = ["SCHEMA", "decode", "encode", "geometry_model", "geometry_rows", "validate"]

Geometry = (
    PointGeometry
    | MultiPointGeometry
    | LineStringGeometry
    | MultiLineStringGeometry
    | PolygonGeometry
    | MultiPolygonGeometry
)
_ADAPTER = TypeAdapter(Annotated[Geometry, Field(discriminator="type")])


def encode(geometries: Iterable[Geometry | dict[str, Any]]) -> pa.Table:
    """Validate models and convert through Shapely's geometry implementation.

    Returns:
        A binary Arrow column containing WKB.
    """
    values = [_ADAPTER.validate_python(value).model_dump() for value in geometries]
    return ingest_geometries(
        np.asarray([shape(value) for value in values], dtype=object)
    )


def geometry_model(raw: dict[str, Any]) -> Geometry:
    """Construct a detached geometry model.

    Returns:
        The concrete geometry model with independent coordinate lists.
    """
    return _ADAPTER.validate_python(raw)


def geometry_rows(table: pa.Table) -> Iterator[dict[str, Any]]:
    """Yield detached models in bounded WKB batches."""
    for batch in table.to_batches(max_chunksize=4096):
        for geometry in shapely.from_wkb(
            batch.column(0).to_numpy(zero_copy_only=False)
        ):
            yield geometry_model(mapping(geometry)).model_dump()


def decode(table: pa.Table) -> list[Geometry]:
    """Validate WKB and return independent models.

    Returns:
        Geometry models in stored row order.
    """
    validate(table)
    return [geometry_model(row) for row in geometry_rows(table)]
