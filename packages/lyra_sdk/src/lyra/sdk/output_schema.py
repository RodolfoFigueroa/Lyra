"""Compile executable Pandera contracts and generate descriptive metadata."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date
from typing import Any

import numpy as np
import pandera.pandas as pa
from lyra.sdk.models.output import ColumnMetadata, OutputMetadata
from lyra.sdk.units import Unit
from pandera.engines import geopandas_engine, numpy_engine, pandas_engine

OutputSchema = pa.DataFrameSchema | type[pa.DataFrameModel]

_SUPPORTED_DTYPES = frozenset(
    type(pa.Column(dtype).dtype)
    for dtype in (
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float16",
        "float32",
        "float64",
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "Float32",
        "Float64",
        "bool",
        "boolean",
        "str",
        "string",
        "category",
    )
) | {
    numpy_engine.String,
    pandas_engine.Date,
    pandas_engine.DateTime,
    geopandas_engine.Geometry,
}


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
    component: pa.Column | pa.Index | pa.DataFrameSchema,
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


def _dtype(component: pa.Column | pa.Index | pa.DataFrameSchema) -> str:
    dtype = component.dtype
    name = str(dtype)
    if type(dtype) not in _SUPPORTED_DTYPES:
        msg = f"Unsupported output dtype: {dtype}"
        raise ValueError(msg)
    categories = getattr(dtype, "categories", None)
    if categories is not None:
        for item in categories:
            category = item.item() if isinstance(item, np.generic) else item
            if not isinstance(category, str | bool | int | float | date):
                msg = "Categorical values must be supported scalars"
                raise TypeError(msg)
            _json_value(category)
    return name


def _component(component: pa.Column | pa.Index) -> dict[str, Any]:
    if component.parsers or component.drop_invalid_rows:
        msg = "Output schemas cannot parse data or drop rows"
        raise ValueError(msg)
    return {
        "dtype": None
        if isinstance(component, pa.Index) and component.dtype is None
        else _dtype(component),
        "nullable": component.nullable,
        "unique": component.unique,
        "coerce": component.coerce,
        "checks": _checks(component),
        "parameters": {
            key: _json_value(getattr(component.dtype, key))
            for key in (
                "categories",
                "ordered",
                "unit",
                "storage",
                "time_zone_agnostic",
            )
            if getattr(component.dtype, key, None) is not None
        }
        | {
            key: str(getattr(component.dtype, key))
            for key in ("tz", "crs")
            if getattr(component.dtype, key, None) is not None
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
    schema.strict = True
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
        unit = column.metadata["unit"]
        data = _component(column)
        if data["dtype"] == "geometry":
            if geometry is not None:
                msg = "Only one geometry column is supported"
                raise ValueError(msg)
            geometry = name
        columns.append(
            ColumnMetadata(
                name=name,
                description=column.description,
                unit=None if unit is None else Unit(unit),
                **data,
            )
        )
    indices = (
        [] if schema.index is None else getattr(schema.index, "indexes", [schema.index])
    )
    return schema, OutputMetadata(
        columns=columns,
        geometry=geometry,
        checks=_checks(schema),
        ordered=schema.ordered,
        coerce=schema.coerce,
        unique=schema.unique,
        index={
            "kind": "multiindex"
            if isinstance(schema.index, pa.MultiIndex)
            else "index",
            "levels": [{"name": index.name, **_component(index)} for index in indices],
            "ordered": getattr(schema.index, "ordered", True),
            "strict": getattr(schema.index, "strict", False),
            "coerce": schema.index.coerce,
            "unique": schema.index.unique
            if isinstance(schema.index, pa.MultiIndex)
            else None,
        }
        if schema.index is not None
        else None,
    )
