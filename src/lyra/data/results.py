"""Native result values, intrinsic checks and public JSON serialization."""

from __future__ import annotations

import json
from datetime import date
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

import geopandas as gpd
import numpy as np
from shapely import get_coordinates

from lyra.contracts.errors import MetricResultError
from lyra.data.scalars import date_property

if TYPE_CHECKING:
    import pandas as pd

    from lyra.contracts.types import JsonValue

NativeOutputs: TypeAlias = "dict[str, pd.DataFrame | gpd.GeoDataFrame]"


class _CellValidationError(ValueError):
    def __init__(self, column: str, check: str, message: str) -> None:
        self.column = column
        self.check = check
        super().__init__(message)


def validate_serializable(table: pd.DataFrame) -> None:
    """Check intrinsic native value constraints without a producer schema.

    Raises:
        _CellValidationError: If native values are unsupported.
    """
    if (
        isinstance(table, gpd.GeoDataFrame)
        and np.isinf(
            get_coordinates(table.geometry.to_numpy(copy=False), include_z=True)
        ).any()
    ):
        key = str(table.geometry.name)
        msg = f"Column {key!r} contains infinity"
        raise _CellValidationError(key, "finite", msg)
    _validate_categories(table)
    for key in table.select_dtypes(include="number"):
        if np.isinf(table[key].dropna()).any():
            msg = f"Column {key!r} contains infinity"
            raise _CellValidationError(str(key), "finite", msg)


def _validate_categories(validated: pd.DataFrame) -> None:
    for key in validated.select_dtypes(include="category"):
        _validate_scalar_values(validated[key].cat.categories, str(key))
        for item in validated[key].cat.categories:
            value = item.item() if isinstance(item, np.generic) else item
            if not isinstance(value, str | bool | int | float | date):
                msg = f"Column {key!r} contains unsupported categorical values"
                raise _CellValidationError(str(key), "supported_scalar", msg)
            if isinstance(value, float) and not np.isfinite(value):
                msg = f"Column {key!r} contains infinity"
                raise _CellValidationError(str(key), "finite", msg)


def _validate_scalar_values(values: object, label: str) -> None:
    pd = import_module("pandas")
    kind = pd.api.types.infer_dtype(values, skipna=True)
    if kind not in {
        "integer",
        "floating",
        "boolean",
        "string",
        "unicode",
        "date",
        "datetime",
        "datetime64",
        "empty",
    }:
        raise _CellValidationError(
            label, "storage_type", "unsupported values: expected compatible scalars"
        )


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
            default=date_property,
            allow_nan=False,
        )
    return table.to_json(
        orient="records", date_format="iso", default_handler=date_property
    )
