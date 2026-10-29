"""Native node result checks and public table serialization."""

from __future__ import annotations

import json
from datetime import date, datetime
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

import geopandas as gpd
import numpy as np
from lyra.sdk.errors import MetricResultError
from lyra.sdk.output_schema import compile_output
from pandera.config import ValidationDepth, config_context
from pandera.errors import SchemaError, SchemaErrors
from shapely import get_coordinates

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd
    import pandera.pandas as pa
    from lyra.sdk.output_schema import OutputSchema
    from lyra.sdk.types import JsonValue

NativeOutputs: TypeAlias = "dict[str, pd.DataFrame | gpd.GeoDataFrame]"


class _CellValidationError(ValueError):
    def __init__(self, column: str, check: str, message: str) -> None:
        self.column = column
        self.check = check
        super().__init__(message)


def normalize_native_result(
    node: str,
    outputs: Mapping[str, OutputSchema],
    value: object,
    *,
    lazy: bool = False,
) -> NativeOutputs:
    """Compile explicit declarations and validate a native result.

    Returns:
        Named tables satisfying the registered contract.
    """
    return validate_registered_result(
        node,
        {name: compile_output(schema)[0] for name, schema in outputs.items()},
        value,
        lazy=lazy,
    )


def validate_registered_result(
    node: str,
    outputs: Mapping[str, pa.DataFrameSchema],
    value: object,
    *,
    lazy: bool = False,
) -> NativeOutputs:
    """Validate return shape, schemas, and serialization requirements.

    Returns:
        Named validated tables, including explicitly requested coercion.

    Raises:
        MetricResultError: If output names or native table types are invalid.
    """
    if not outputs:
        raise MetricResultError(node, "outputs", "at least one output is required")
    if len(outputs) == 1:
        values = {next(iter(outputs)): value}
    elif isinstance(value, dict) and value.keys() == outputs.keys():
        values = value
    else:
        raise MetricResultError(
            node, "outputs", f"expected a dictionary with exactly {list(outputs)!r}"
        )
    result: NativeOutputs = {}
    errors: list[MetricResultError] = []
    for name, schema in outputs.items():
        try:
            result[name] = _validate_table(node, name, schema, values[name], lazy=lazy)
        except MetricResultError as exc:
            if not lazy:
                raise
            errors.append(exc)
    if errors:
        failure = MetricResultError(
            node,
            "outputs",
            "Output validation failed",
            failures=[entry for error in errors for entry in error.failures],
        )
        failure.truncated = failure.truncated or any(
            error.truncated for error in errors
        )
        raise failure from errors[0].__cause__
    return result


def _validate_table(
    node: str,
    name: str,
    schema: pa.DataFrameSchema,
    table: object,
    *,
    lazy: bool,
) -> pd.DataFrame:
    pd = import_module("pandas")
    if not isinstance(table, pd.DataFrame):
        raise MetricResultError(
            node,
            f"output {name!r}",
            "expected a DataFrame or GeoDataFrame",
            failures=[
                {
                    "output": name,
                    "column": None,
                    "check": "table_type",
                    "message": "expected a DataFrame or GeoDataFrame",
                }
            ],
        )
    try:
        with config_context(
            validation_enabled=True, validation_depth=ValidationDepth.SCHEMA_AND_DATA
        ):
            validated = schema.validate(table, lazy=lazy)
        _validate_serializable(validated, schema)
    except (SchemaError, SchemaErrors) as exc:
        errors = getattr(exc, "schema_errors", [exc])
        failures = [
            {
                "output": name,
                "column": getattr(error, "column_name", None)
                or getattr(error.schema, "name", None),
                "check": getattr(error.check, "name", None) or str(error.check),
                "message": str(error),
            }
            for error in errors
        ]
        raise MetricResultError(
            node, f"output {name!r}", str(exc), failures=failures
        ) from exc
    except (ValueError, TypeError) as exc:
        raise MetricResultError(
            node,
            f"output {name!r}",
            str(exc),
            failures=[
                {
                    "output": name,
                    "column": getattr(exc, "column", None),
                    "check": getattr(exc, "check", "serialization"),
                    "message": str(exc),
                }
            ],
        ) from exc
    return validated


def _validate_serializable(validated: pd.DataFrame, schema: pa.DataFrameSchema) -> None:
    geometry = [
        key for key, column in schema.columns.items() if str(column.dtype) == "geometry"
    ]
    if geometry:
        if (
            not isinstance(validated, gpd.GeoDataFrame)
            or validated.active_geometry_name != geometry[0]
            or validated.crs is None
        ):
            msg = "Expected GeoDataFrame with declared active geometry and known CRS"
            raise ValueError(msg)
        if np.isinf(
            get_coordinates(validated.geometry.to_numpy(copy=False), include_z=True)
        ).any():
            msg = f"Column {geometry[0]!r} contains infinity"
            raise _CellValidationError(geometry[0], "finite", msg)
    elif isinstance(validated, gpd.GeoDataFrame):
        msg = "Spatial output requires a declared geometry column"
        raise ValueError(msg)
    _validate_categories(validated)
    for key in validated.select_dtypes(include="number"):
        if np.isinf(validated[key].dropna()).any():
            msg = f"Column {key!r} contains infinity"
            raise _CellValidationError(str(key), "finite", msg)


def _validate_categories(validated: pd.DataFrame) -> None:
    for key in validated.select_dtypes(include="category"):
        for item in validated[key].cat.categories:
            value = item.item() if isinstance(item, np.generic) else item
            if not isinstance(value, str | bool | int | float | date):
                msg = f"Column {key!r} contains unsupported categorical values"
                raise _CellValidationError(str(key), "supported_scalar", msg)
            if isinstance(value, float) and not np.isfinite(value):
                msg = f"Column {key!r} contains infinity"
                raise _CellValidationError(str(key), "finite", msg)


def _date_property(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    msg = f"unsupported value of type {type(value).__name__}"
    raise TypeError(msg)


def serialize_outputs(node: str, outputs: NativeOutputs) -> dict[str, JsonValue]:
    """Encode named tables as JSON arrays or GeoJSON objects.

    Returns:
        JSON-compatible named outputs with no index or inferred row identity.

    Raises:
        MetricResultError: If a table cannot be serialized or has unknown CRS.
    """
    encoded: dict[str, JsonValue] = {}
    for name, table in outputs.items():
        try:
            encoded[name] = json.loads(_encode_table(table))
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise MetricResultError(
                node, f"output {name!r} serialization", str(exc)
            ) from exc
    return encoded


def _encode_table(table: pd.DataFrame | gpd.GeoDataFrame) -> str:
    if isinstance(table, gpd.GeoDataFrame):
        if table.crs is None:
            msg = "GeoJSON serialization requires a known CRS"
            raise ValueError(msg)
        return table.to_json(
            drop_id=True,
            na="null",
            to_wgs84=True,
            default=_date_property,
            allow_nan=False,
        )
    return table.to_json(
        orient="records", date_format="iso", default_handler=_date_property
    )
