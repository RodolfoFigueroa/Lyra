"""Compile executable producer declarations into native storage inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow  # ruff: ignore[unconventional-import-alias] - distinguish native and executable schemas
from pandera.engines import geopandas_engine, pandas_engine

from lyra.data import arrow_conversion
from lyra.data.relational import StorageDescription
from lyra.sdk.output_schema import compile_output
from lyra.sdk.results import validate_registered_result

if TYPE_CHECKING:
    from lyra.contracts.outputs import OutputMetadata
    from lyra.sdk.output_schema import OutputSchema


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
    return arrow_conversion.to_arrow(
        empty, pyarrow.schema(fields, metadata=native.metadata)
    ).schema


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


def empty_frame(declaration: pandera.DataFrameSchema) -> pd.DataFrame:
    """Construct declared empty native columns, including spatial CRS.

    Returns:
        A fresh typed frame with every required column.
    """
    data = {
        name: pd.Series([], dtype=column.dtype.type)
        for name, column in declaration.columns.items()
        if not isinstance(column.dtype, geopandas_engine.Geometry)
    }
    geometry = declaration.columns.get("geometry")
    if geometry is not None and isinstance(geometry.dtype, geopandas_engine.Geometry):
        data["geometry"] = gpd.GeoSeries([], crs=geometry.dtype.crs)
        return gpd.GeoDataFrame(data, geometry="geometry")[list(declaration.columns)]
    return pd.DataFrame(data)


_TYPES = {
    "str": ("string", "TEXT"),
    "string": ("string", "TEXT"),
    "string[pyarrow]": ("string", "TEXT"),
    "string[python]": ("string", "TEXT"),
    "int64": ("Int64", "INTEGER"),
    "Int64": ("Int64", "INTEGER"),
    "bool": ("boolean", "INTEGER"),
    "boolean": ("boolean", "INTEGER"),
    "float64": ("Float64", "REAL"),
    "Float64": ("Float64", "REAL"),
    "geometry": ("geometry", "BLOB"),
}


def describe(
    declaration: OutputSchema, *, metadata: OutputMetadata | None = None
) -> StorageDescription:
    """Compile a fixed relational layout without inspecting rows or services.

    Returns:
        Immutable scalar and spatial reconstruction metadata.

    Raises:
        ValueError: If columns or declared types cannot be stored.
    """
    if not isinstance(declaration, pandera.DataFrameSchema):
        declaration = declaration.to_schema()
    if declaration.strict is not True or any(
        not isinstance(name, str)
        or not name.strip()
        or column.regex
        or not column.required
        for name, column in declaration.columns.items()
    ):
        msg = "Storage requires strict fixed required columns"
        raise ValueError(msg)
    types = []
    for column in declaration.columns.values():
        kind = _TYPES.get(str(column.dtype))
        if kind is None:
            msg = f"Unsupported storage dtype: {column.dtype}"
            raise ValueError(msg)
        types.append(kind)
    spatial = [
        name
        for name, column in declaration.columns.items()
        if isinstance(column.dtype, geopandas_engine.Geometry)
    ]
    if spatial and (
        spatial != ["geometry"] or declaration.columns["geometry"].dtype.crs is None
    ):
        msg = "Storage geometry requires active geometry and declared CRS"
        raise ValueError(msg)
    geometry = declaration.columns.get("geometry") if spatial else None
    return StorageDescription(
        metadata=metadata,
        properties=arrow_schema(declaration),
        columns=tuple(declaration.columns),
        pandas_dtypes=tuple(kind[0] for kind in types),
        sqlite_types=tuple(kind[1] for kind in types),
        nullable=tuple(column.nullable for column in declaration.columns.values()),
        crs=str(geometry.dtype.crs) if geometry is not None else None,
        geometry="geometry" if spatial else None,
        descriptions=tuple(
            column.description for column in declaration.columns.values()
        ),
        units=tuple(
            None
            if (column.metadata or {}).get("unit") is None
            else str(column.metadata["unit"])
            for column in declaration.columns.values()
        ),
    )


def prepare_native(
    frame: pd.DataFrame, declaration: OutputSchema
) -> tuple[pd.DataFrame, StorageDescription]:
    """Validate external input and acquire an owned frame with its matching layout.

    Registered producers already perform this validation; private writers consume
    their owned frames and compiled descriptions directly. This entry is for
    callers that have not crossed that boundary.

    Returns:
        Detached validated native values and their immutable declared layout.
    """
    schema, metadata = compile_output(declaration)
    layout = describe(schema, metadata=metadata)
    return validate_registered_result("storage", {"value": schema}, frame)[
        "value"
    ], layout
