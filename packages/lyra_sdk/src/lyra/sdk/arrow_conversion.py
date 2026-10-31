"""Declaration-derived conversion for validated non-geometry columns.

Geometry remains the responsibility of the existing geometry storage path.
Native Arrow categorical conversion can lose unused categories on empty tables;
no categorical repair encoding is supplied here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
from pandera.engines import geopandas_engine, pandas_engine

if TYPE_CHECKING:
    import pandera.pandas as pandera


def arrow_schema(declaration: pandera.DataFrameSchema) -> pa.Schema:
    """Derive storage fields and pandas metadata without inspecting output rows.

    Returns:
        A schema containing only declared non-geometry fields.

    Raises:
        ValueError: If categorical values have no declared type.
    """
    columns = {
        name: column
        for name, column in declaration.columns.items()
        if not isinstance(column.dtype, geopandas_engine.Geometry)
    }
    empty = pd.DataFrame(
        {
            name: pd.Series([], dtype=column.dtype.type)
            for name, column in columns.items()
        }
    )
    native = pa.Schema.from_pandas(empty, preserve_index=False)
    fields = []
    for field in native:
        column = columns[field.name]
        dtype = field.type
        if pa.types.is_null(dtype):
            if isinstance(column.dtype, pandas_engine.Date):
                dtype = pa.date32()
            elif str(column.dtype) == "str":
                dtype = pa.string()
        if pa.types.is_dictionary(dtype) and pa.types.is_null(dtype.value_type):
            msg = f"Column {field.name!r} requires declared categorical values"
            raise ValueError(msg)
        fields.append(pa.field(field.name, dtype, nullable=column.nullable))
    return pa.schema(fields, metadata=native.metadata)


def to_arrow(frame: pd.DataFrame, declaration: pandera.DataFrameSchema) -> pa.Table:
    """Validate a copy, then convert declared properties without storage coercion.

    Pandera alone applies any explicitly declared coercion. Indexes are discarded
    before validation; geometry is validated but left to its existing codec.

    Returns:
        Typed properties with native pandas reconstruction metadata.

    Raises:
        ValueError: If columns differ, infinity occurs, or nullability is violated.
    """
    if not frame.columns.is_unique or set(frame.columns) != set(declaration.columns):
        msg = "Expected exactly the declared columns"
        raise ValueError(msg)
    normalized = frame.loc[:, list(declaration.columns)].reset_index(drop=True).copy()
    validated = declaration.validate(normalized)
    schema = arrow_schema(declaration)
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
