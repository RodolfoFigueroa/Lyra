"""Compile executable producer declarations into native storage inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow  # ruff: ignore[unconventional-import-alias] - distinguish native and executable schemas
from pandera.engines import geopandas_engine, pandas_engine
from pydantic import TypeAdapter, ValidationError

from lyra.contracts.units import ColumnUnit
from lyra.data import arrow_conversion
from lyra.data.relational import StorageDescription, logical_dtype
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


_SQLITE_TYPES = {
    "string": "TEXT",
    "Int64": "INTEGER",
    "boolean": "INTEGER",
    "Float64": "REAL",
    "geometry": "BLOB",
}


_COLUMN_UNIT = TypeAdapter(ColumnUnit)


def _storage_units(
    declaration: pandera.DataFrameSchema, metadata: OutputMetadata | None
) -> tuple[str | None, ...]:
    if metadata is not None:
        by_name = {column.name: column.unit for column in metadata.columns}
        return tuple(by_name[name] for name in declaration.columns)

    units = []
    for name, column in declaration.columns.items():
        if "unit" not in (column.metadata or {}):
            message = f"Column {name!r} requires metadata['unit']"
            raise ValueError(message)
        try:
            units.append(_COLUMN_UNIT.validate_python(column.metadata["unit"]))
        except ValidationError as error:
            message = f"Column {name!r} has invalid unit metadata: {error}"
            raise ValueError(message) from error
    return tuple(units)


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
    types = tuple(
        logical_dtype(str(column.dtype)) for column in declaration.columns.values()
    )
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
        pandas_dtypes=types,
        sqlite_types=tuple(_SQLITE_TYPES[dtype] for dtype in types),
        nullable=tuple(column.nullable for column in declaration.columns.values()),
        crs=str(geometry.dtype.crs) if geometry is not None else None,
        geometry="geometry" if spatial else None,
        descriptions=tuple(
            column.description for column in declaration.columns.values()
        ),
        units=_storage_units(declaration, metadata),
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
