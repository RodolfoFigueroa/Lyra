"""Native Arrow conversion for validated non-geometry columns.

Geometry remains the responsibility of the existing geometry storage path.
Native Arrow categorical conversion can lose unused categories on empty tables;
no categorical repair encoding is supplied here.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa


def to_arrow(
    frame: pd.DataFrame, schema: pa.Schema, *, columns: tuple[str, ...] | None = None
) -> pa.Table:
    """Convert native columns, checking shape, finite values and nullability.

    ``columns`` includes geometry when the caller stores it separately.

    Returns:
        Typed non-geometry columns with pandas reconstruction metadata.

    Raises:
        ValueError: If columns differ, infinity occurs, or nullability is violated.
    """
    columns = tuple(schema.names) if columns is None else columns
    if (
        len(set(columns)) != len(columns)
        or not frame.columns.is_unique
        or set(frame.columns) != set(columns)
        or tuple(name for name in columns if name in schema.names)
        != tuple(schema.names)
    ):
        msg = "Expected exactly the declared columns"
        raise ValueError(msg)
    validated = frame.loc[:, list(columns)].reset_index(drop=True)
    arrays = []
    for field in schema:
        series = validated[field.name]
        if pd.api.types.is_numeric_dtype(series.dtype) and np.isinf(series).any():
            msg = f"Column {field.name!r} contains infinity"
            raise ValueError(msg)
        array = pa.array(series, type=field.type, from_pandas=True, safe=True)
        if not field.nullable and array.null_count:
            msg = f"Column {field.name!r} is not nullable"
            raise ValueError(msg)
        arrays.append(array)
    # Native metadata retains pandas extension details (including string storage
    # and null markers) that Pandera's dtype.type may not reproduce.
    metadata = pa.Schema.from_pandas(
        validated.loc[:, schema.names], preserve_index=False
    ).metadata
    if metadata is not None:
        pandas_metadata = json.loads(metadata[b"pandas"])
        for column in pandas_metadata["columns"]:
            dtype = validated[column["field_name"]].dtype
            if isinstance(dtype, pd.StringDtype) and dtype.na_value is pd.NA:
                column["numpy_type"] = f"string[{dtype.storage}]"
        metadata[b"pandas"] = json.dumps(pandas_metadata).encode()
    if not arrays:
        return (
            pa.table({"_rows": pa.nulls(len(validated))})
            .replace_schema_metadata(metadata)
            .select([])
        )
    return pa.Table.from_arrays(arrays, schema=schema.with_metadata(metadata))


def from_arrow(table: pa.Table) -> pd.DataFrame:
    """Restore native declared pandas types using Arrow's pandas metadata.

    Returns:
        A native frame with a fresh positional index.
    """
    return table.to_pandas().reset_index(drop=True)
