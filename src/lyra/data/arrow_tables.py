"""Declared Arrow schemas and batches for retained relational table transport."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import shapely
from pyproj import CRS

from lyra.contracts.provider_scope import EffectiveScope
from lyra.data.arrow_conversion import to_arrow
from lyra.data.relational import checked_column

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
    arrays = []
    for i, field in enumerate(schema):
        values = checked_column(rows, i, description)
        if field.name == description.geometry:
            shapely.from_wkb(np.asarray(values, dtype=object))
        if pa.types.is_boolean(field.type):
            values = [None if value is None else bool(value) for value in values]
        arrays.append(pa.array(values, type=field.type))
    if not arrays:
        batch = pa.record_batch([pa.nulls(len(rows))], names=["_rows"]).select([])
        return batch.replace_schema_metadata(schema.metadata)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)
