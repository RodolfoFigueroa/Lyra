"""Compile executable producer declarations into native storage inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow  # ruff: ignore[unconventional-import-alias] - distinguish native and executable schemas
from pandera.engines import geopandas_engine, pandas_engine

from lyra.data import arrow_conversion

if TYPE_CHECKING:
    import pandera.pandas as pandera


def arrow_schema(declaration: pandera.DataFrameSchema) -> pyarrow.Schema:
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
    native = pyarrow.Schema.from_pandas(empty, preserve_index=False)
    fields = []
    for field in native:
        column = columns[field.name]
        dtype = field.type
        if pyarrow.types.is_null(dtype):
            if isinstance(column.dtype, pandas_engine.Date):
                dtype = pyarrow.date32()
            elif str(column.dtype) == "str":
                dtype = pyarrow.string()
        if pyarrow.types.is_dictionary(dtype) and pyarrow.types.is_null(
            dtype.value_type
        ):
            msg = f"Column {field.name!r} requires declared categorical values"
            raise ValueError(msg)
        fields.append(pyarrow.field(field.name, dtype, nullable=column.nullable))
    return pyarrow.schema(fields, metadata=native.metadata)


@dataclass(frozen=True)
class StorageInput:
    """Validated frame and declarative native storage layout."""

    frame: pd.DataFrame
    schema: pyarrow.Schema
    columns: tuple[str, ...]


def prepare(frame: pd.DataFrame, declaration: pandera.DataFrameSchema) -> StorageInput:
    """Validate an independent positional frame and compile its storage schema.

    Returns:
        Validated data and its native schema and column order.

    Raises:
        ValueError: If columns do not match the declaration.
    """
    if not frame.columns.is_unique or set(frame.columns) != set(declaration.columns):
        msg = "Expected exactly the declared columns"
        raise ValueError(msg)
    normalized = frame.loc[:, list(declaration.columns)].reset_index(drop=True).copy()
    validated = declaration.validate(normalized)
    return StorageInput(
        validated, arrow_schema(declaration), tuple(declaration.columns)
    )


def to_arrow(
    frame: pd.DataFrame, declaration: pandera.DataFrameSchema
) -> pyarrow.Table:
    """Validate producer data and return native typed properties.

    Returns:
        Typed properties with pandas reconstruction metadata.
    """
    prepared = prepare(frame, declaration)
    return arrow_conversion.to_arrow(
        prepared.frame, prepared.schema, columns=prepared.columns
    )
