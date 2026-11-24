"""Compile executable Pandera contracts and generate descriptive metadata."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.engines import geopandas_engine, numpy_engine, pandas_engine

from lyra.contracts.outputs import ColumnMetadata, OutputMetadata

if TYPE_CHECKING:
    from lyra.contracts.metric_scope import OutputScope

OutputSchema = pa.DataFrameSchema | type[pa.DataFrameModel]

_SUPPORTED_DTYPES = frozenset(
    type(pa.Column(dtype).dtype)
    for dtype in (
        "int64",
        "Int64",
        "float64",
        "Float64",
        "bool",
        "boolean",
        "str",
        "string",
    )
) | {geopandas_engine.Geometry}


def _json_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        msg = "Metadata numbers must be finite"
        raise ValueError(msg)
    if isinstance(value, re.Pattern):
        value = {"pattern": value.pattern, "flags": value.flags}
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_json_value(item) for item in value), key=repr)
    msg = f"Unsupported metadata value: {type(value).__name__}"
    raise ValueError(msg)


def _checks(
    component: pa.Column | pa.DataFrameSchema,
) -> list[dict[str, Any]]:
    result = []
    for check in component.checks or []:
        if check.raise_warning or not check.name or check.name == "<lambda>":
            msg = "Checks must have stable names and must fail on errors"
            raise ValueError(msg)
        result.append(
            {
                "name": check.name,
                "description": check.description,
                "kind": "builtin"
                if pa.Check.is_builtin_check(check.name)
                else "python",
                "parameters": _json_value(check.statistics or {})
                if pa.Check.is_builtin_check(check.name)
                else {},
                "options": {
                    "ignore_na": check.ignore_na,
                    "element_wise": check.element_wise,
                },
            }
        )
    return result


def _compile_column(column: pa.Column) -> dict[str, Any]:
    if column.parsers or column.drop_invalid_rows:
        msg = "Output schemas cannot parse data or drop rows"
        raise ValueError(msg)
    # The NumPy engine accepts any object array. The pandas engine also
    # checks that its non-null cells are strings.
    if type(column.dtype) is numpy_engine.String:
        column.dtype = "string"
    if type(column.dtype) not in _SUPPORTED_DTYPES:
        msg = f"Unsupported output dtype: {column.dtype}"
        raise ValueError(msg)
    return {
        "dtype": str(column.dtype),
        "nullable": column.nullable,
        "unique": column.unique,
        "checks": _checks(column),
        "parameters": {
            key: _json_value(getattr(column.dtype, key))
            for key in (
                "categories",
                "ordered",
                "unit",
                "storage",
                "time_zone_agnostic",
            )
            if getattr(column.dtype, key, None) is not None
        }
        | {
            key: str(getattr(column.dtype, key))
            for key in ("tz", "crs")
            if getattr(column.dtype, key, None) is not None
        },
    }


def compile_output(
    declaration: OutputSchema,
) -> tuple[pa.DataFrameSchema, OutputMetadata]:
    """Snapshot a schema and generate JSON metadata.

    Returns:
        An independent executable schema and its descriptive metadata.

    Raises:
        ValueError: If the declaration cannot enforce a serializable contract.
        TypeError: If the declaration is not a supported Pandera schema.
    """
    if isinstance(declaration, type) and issubclass(declaration, pa.DataFrameModel):
        declaration = declaration.to_schema()
    if not isinstance(declaration, pa.DataFrameSchema):
        msg = "Outputs require Pandera DataFrameSchema or DataFrameModel declarations"
        raise TypeError(msg)
    if declaration.index is not None:
        msg = (
            "Output schemas cannot declare indexes; "
            "declare identity and uniqueness in columns"
        )
        raise ValueError(msg)
    _validate_producer_schema(declaration)
    schema = deepcopy(declaration)
    if (
        schema.strict == "filter"
        or schema.add_missing_columns
        or schema.parsers
        or schema.drop_invalid_rows
        or schema.dtype is not None
    ):
        msg = "Output schemas cannot filter, add columns, parse data, or drop rows"
        raise ValueError(msg)
    schema.unique_column_names = True
    columns = []
    geometry = None
    for name, column in schema.columns.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or column.regex
            or not column.required
        ):
            msg = "Output columns must have fixed, required string names"
            raise ValueError(msg)
        if (
            not column.description
            or not column.description.strip()
            or "unit" not in (column.metadata or {})
        ):
            msg = f"Column {name!r} requires a description and metadata['unit']"
            raise ValueError(msg)
        data = _compile_column(column)
        if data["dtype"] == "geometry":
            if geometry is not None:
                msg = "Only one geometry column is supported"
                raise ValueError(msg)
            _validate_geometry(name, column)
            geometry = name
        metadata = ColumnMetadata(
            name=name,
            description=column.description,
            unit=column.metadata["unit"],
            **data,
        )
        column.metadata["unit"] = metadata.unit
        columns.append(metadata)
    return schema, OutputMetadata(
        columns=columns,
        meaning=(schema.metadata or {}).get("meaning"),
        granularity=(schema.metadata or {}).get("granularity"),
        geometry=geometry,
        checks=_checks(schema),
        ordered=schema.ordered,
        unique=schema.unique,
    )


def _validate_geometry(name: str, column: pa.Column) -> None:
    if (
        name != "geometry"
        or getattr(column.dtype, "crs", None) is None
        or not column.checks
    ):
        msg = "geometry must declare its fixed name, expected CRS, and geometry checks"
        raise ValueError(msg)


def _validate_producer_schema(declaration: pa.DataFrameSchema) -> None:
    if declaration.coerce or any(col.coerce for col in declaration.columns.values()):
        msg = "Producer schemas and columns cannot use coerce=True"
        raise ValueError(msg)
    if not declaration.columns:
        msg = "Each output must declare at least one column"
        raise ValueError(msg)
    if any(
        not pd.api.types.is_scalar(col.default) or not pd.isna(col.default)
        for col in declaration.columns.values()
    ):
        msg = "Producer columns cannot supply fill defaults"
        raise ValueError(msg)
    if declaration.strict is not True:
        msg = "Output schemas require strict=True"
        raise ValueError(msg)


def validate_metric_output(schema: pa.DataFrameSchema, scope: OutputScope) -> None:
    """Require structural columns to match the metric's declared ownership.

    Raises:
        ValueError: If reserved names or identity constraints are invalid.
    """
    reserved = {"source_location_id"}
    if scope == "unpartitioned":
        reserved.update({"location_id", "row_key"})
    if reserved & schema.columns.keys():
        msg = "Metric output uses reserved structural column names"
        raise ValueError(msg)
    geometry = schema.columns.get("geometry")
    if geometry is not None and not isinstance(
        geometry.dtype, geopandas_engine.Geometry
    ):
        msg = "geometry is reserved for spatial geometry values"
        raise ValueError(msg)
    if scope == "unpartitioned":
        return
    for name in ("location_id", "row_key"):
        column = schema.columns.get(name)
        if (
            column is None
            or column.nullable
            or not isinstance(column.dtype, numpy_engine.String | pandas_engine.STRING)
        ):
            msg = f"Location-scoped outputs require non-null string {name}"
            raise ValueError(msg)
    unique = schema.unique or []
    groups = [unique] if unique and isinstance(unique[0], str) else unique
    if not any(set(group) == {"location_id", "row_key"} for group in groups):
        msg = (
            "Location-scoped outputs require composite uniqueness "
            "on location_id and row_key"
        )
        raise ValueError(msg)
