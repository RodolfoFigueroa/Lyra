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
from lyra.data.relational import checked_column

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lyra.contracts.provider_scope import DatasetScope
    from lyra.data.relational import Cell, StorageDescription


def base_schema(description: StorageDescription) -> pa.Schema:
    """Build the scope-independent download schema from logical columns.

    Returns:
        Ordered Arrow fields and native pandas reconstruction metadata.
    """
    scalars = pd.DataFrame(
        {
            name: pd.Series(dtype="string[python]" if dtype == "string" else dtype)
            for name, dtype in zip(
                description.columns, description.pandas_dtypes, strict=True
            )
            if dtype != "geometry"
        }
    )
    properties = pa.Schema.from_pandas(scalars, preserve_index=False)
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
            field = properties.field(name).with_nullable(nullable)
            metadata = {**(field.metadata or {}), **metadata}
        fields.append(field.with_metadata(metadata))
    metadata = dict(properties.metadata or {})
    if description.metadata is not None:
        for name in ("meaning", "granularity"):
            value = getattr(description.metadata, name)
            if value is not None:
                metadata[name.encode()] = value.encode()
    return pa.schema(fields, metadata=metadata)


def table_schema(schema: pa.Schema, **scopes: DatasetScope | None) -> pa.Schema:
    """Attach the selected intervention scopes for this download.

    Returns:
        An Arrow schema with the applicable scope metadata.
    """
    if not any(isinstance(scope, EffectiveScope) for scope in scopes.values()):
        return schema
    metadata = dict(schema.metadata or {})
    metadata.update(
        {
            role.encode(): scope.model_dump_json().encode()
            for role, scope in scopes.items()
            if scope is not None
        }
    )
    return schema.with_metadata(metadata)


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
    return pa.RecordBatch.from_arrays(arrays, schema=schema)
