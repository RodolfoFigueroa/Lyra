"""Declared Arrow schemas and batches for retained relational table transport."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import shapely
from pyproj import CRS

from lyra.contracts.provider_scope import EffectiveScope
from lyra.data.arrow_conversion import to_arrow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lyra.contracts.provider_scope import DatasetScope
    from lyra.data.relational import Cell, StorageDescription


def table_schema(
    description: StorageDescription, **scopes: DatasetScope | None
) -> pa.Schema:
    """Return declared Arrow fields, GeoArrow CRS and applicable dataset scopes.

    For intervened tables, schema metadata carries the retained JSON scope for
    each ID role, including the ordinary counterpart in a correspondence.
    """
    scalars = pd.DataFrame(
        {
            name: pd.Series(dtype=dtype)
            for name, dtype in zip(
                description.columns, description.pandas_dtypes, strict=True
            )
            if dtype != "geometry"
        }
    )
    properties = to_arrow(scalars, description.properties).schema
    fields = []
    for name, nullable, text, unit in zip(
        description.columns,
        description.nullable,
        description.descriptions,
        description.units,
        strict=True,
    ):
        metadata = {}
        if text is not None:
            metadata[b"description"] = text.encode()
        if unit is not None:
            metadata[b"unit"] = unit.encode()
        if name == description.geometry:
            metadata[b"ARROW:extension:name"] = b"geoarrow.wkb"
            metadata[b"ARROW:extension:metadata"] = json.dumps(
                {
                    "crs": CRS.from_user_input(description.crs).to_json_dict()
                    if description.crs is not None
                    else None
                }
            ).encode()
            field = pa.field(name, pa.binary(), nullable=nullable)
        else:
            field = properties.field(name)
            metadata = {**(field.metadata or {}), **metadata}
        fields.append(field.with_metadata(metadata))
    metadata = {
        **(description.properties.metadata or {}),
        **(properties.metadata or {}),
    }
    if any(isinstance(scope, EffectiveScope) for scope in scopes.values()):
        metadata.update(
            {
                role.encode(): scope.model_dump_json().encode()
                for role, scope in scopes.items()
                if scope is not None
            }
        )
    if description.metadata is not None:
        for name in ("meaning", "granularity"):
            value = getattr(description.metadata, name)
            if value is not None:
                metadata[name.encode()] = value.encode()
    return pa.schema(fields, metadata=metadata)


def table_batch(
    rows: Sequence[tuple[Cell, ...]],
    description: StorageDescription,
    schema: pa.Schema,
) -> pa.RecordBatch:
    """Convert one bounded row batch without JSON or geometry reprojection.

    Returns:
        Declared scalar arrays and unchanged WKB cells in public column order.
    """
    frame = pd.DataFrame(
        {
            name: pd.Series([row[i] for row in rows], dtype=dtype)
            for i, (name, dtype) in enumerate(
                zip(description.columns, description.pandas_dtypes, strict=True)
            )
            if dtype != "geometry"
        },
        index=pd.RangeIndex(len(rows)),
    )
    scalars = to_arrow(frame, description.properties)
    arrays = []
    for i, field in enumerate(schema):
        if field.name == description.geometry:
            cells = pa.array([row[i] for row in rows], type=pa.binary())
            # Arrow binary accepts arbitrary bytes. Check the declared WKB encoding
            # before delivering the original bytes and their GeoArrow metadata.
            shapely.from_wkb(cells.to_numpy(zero_copy_only=False))
            arrays.append(cells)
        else:
            arrays.append(scalars[field.name].combine_chunks())
    if not arrays:
        return scalars.to_batches()[0].replace_schema_metadata(schema.metadata)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)
