"""Producer result validation and coercion against executable schemas."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

import geopandas as gpd
from pandera.config import ValidationDepth, config_context
from pandera.errors import SchemaError, SchemaErrors

from lyra.contracts.errors import MetricResultError
from lyra.data.results import validate_serializable
from lyra.sdk.output_schema import compile_output

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd
    import pandera.pandas as pa

    from lyra.data.results import NativeOutputs
    from lyra.sdk.output_schema import OutputSchema


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
        table = _normalize_frame(table, schema)
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


def _normalize_frame(table: pd.DataFrame, schema: pa.DataFrameSchema) -> pd.DataFrame:
    """Copy declared columns in order and discard incidental row indexes.

    Returns:
        An independent frame with a fresh RangeIndex.

    Raises:
        ValueError: If columns are missing, extra, or duplicated.
    """
    if not table.columns.is_unique or set(table.columns) != set(schema.columns):
        msg = "Output must contain exactly the declared columns without duplicates"
        raise ValueError(msg)
    return table.loc[:, list(schema.columns)].copy(deep=True).reset_index(drop=True)


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
    elif isinstance(validated, gpd.GeoDataFrame):
        msg = "Spatial output requires a declared geometry column"
        raise ValueError(msg)
    validate_serializable(validated)
