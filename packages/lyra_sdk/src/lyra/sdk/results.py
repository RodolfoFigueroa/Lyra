"""Native node result checks and public table serialization."""

from __future__ import annotations

import json
from datetime import date, datetime
from importlib import import_module
from typing import TYPE_CHECKING, TypeAlias

from lyra.sdk.errors import MetricResultError

if TYPE_CHECKING:
    from collections.abc import Mapping

    import geopandas as gpd
    import pandas as pd
    from lyra.sdk.models.plugin import TableOutput
    from lyra.sdk.types import JsonValue

NativeOutputs: TypeAlias = "dict[str, pd.DataFrame | gpd.GeoDataFrame]"


def normalize_native_result(
    node: str, outputs: Mapping[str, TableOutput], value: object
) -> NativeOutputs:
    """Check return shape and retain each producer-owned table by reference.

    Returns:
        Named native tables, without serialization, copying, or schema coercion.

    Raises:
        MetricResultError: If output names or native table types are invalid.
    """
    pd = import_module("pandas")

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
    for name in outputs:
        table = values[name]
        if not isinstance(table, pd.DataFrame):
            raise MetricResultError(
                node, f"output {name!r}", "expected a DataFrame or GeoDataFrame"
            )
        result[name] = table
    return result


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
    gpd = import_module("geopandas")
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
