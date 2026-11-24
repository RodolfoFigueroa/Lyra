"""Compile executable producer declarations into native storage inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
from pandera.engines import geopandas_engine
from pydantic import TypeAdapter, ValidationError

from lyra.contracts.units import ColumnUnit
from lyra.data.relational import StorageDescription, logical_dtype
from lyra.sdk.output_schema import compile_output
from lyra.sdk.results import validate_registered_result

if TYPE_CHECKING:
    from lyra.contracts.outputs import OutputMetadata
    from lyra.sdk.output_schema import OutputSchema


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
