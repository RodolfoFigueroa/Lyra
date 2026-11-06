"""Bounded native-table summaries and shared inspection truncation."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import geopandas as gpd
import pandas as pd
from lyra.sdk.results import serialize_outputs

if TYPE_CHECKING:
    from lyra.sdk.results import NativeOutputs

MAX_INSPECTION_BYTES = 64 * 1024


def json_size(payload: object) -> int:
    """Return the compact UTF-8 JSON size."""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


def summarize_outputs(outputs: NativeOutputs) -> dict[str, Any]:
    """Return a bounded preview and observed statistics without full JSON encoding.

    Raises:
        ValueError: If mandatory output identifiers exceed the size limit.
    """
    result = {}
    remaining_rows, remaining_columns = 5, 20
    for name, table in outputs.items():
        spatial = isinstance(table, gpd.GeoDataFrame)
        geometry = table.geometry.name if spatial else None
        properties = [column for column in table.columns if column != geometry]
        columns = properties[:remaining_columns]
        remaining_columns -= len(columns)
        selected = [*columns, *([geometry] if spatial else [])]
        sample = table.iloc[:remaining_rows][selected]
        remaining_rows -= len(sample)
        preview = cast("Any", serialize_outputs("inspection", {name: sample})[name])
        shortened = 0
        rows = preview["features"] if spatial else preview
        for row in rows:
            values = row["properties"] if spatial else row
            for key, value in values.items():
                if isinstance(value, str) and len(value) > 500:
                    values[key] = value[:499] + "…"
                    shortened += 1
        result[name] = {
            "metadata": {
                "row_count": len(table),
                "column_count": len(properties),
                "spatial": spatial,
                "crs": "EPSG:4326" if spatial else None,
                "geometry_types": sorted(
                    table.geometry.geom_type.dropna().unique().tolist()
                )
                if spatial
                else [],
                "summary": _summaries(table, columns),
            },
            "preview": preview,
            "truncation": {
                "omitted_rows": len(table) - len(sample),
                "omitted_columns": len(properties) - len(columns),
                "shortened_strings": shortened,
                "omitted_sections": [],
            },
        }
    payload = {"outputs": result, "truncation": {"omitted_sections": []}}
    while json_size(payload) > MAX_INSPECTION_BYTES and trim_inspection(payload):
        pass
    if json_size(payload) > MAX_INSPECTION_BYTES:
        message = "Output identifiers exceed the inspection size limit"
        raise ValueError(message)
    return result


def _summaries(table: pd.DataFrame, columns: list[str]) -> dict:
    summaries = {}
    for column in columns:
        series = table[column]
        present = series.dropna()
        numeric = pd.api.types.infer_dtype(present, skipna=True) in {
            "integer",
            "floating",
            "mixed-integer-float",
            "decimal",
        }
        summaries[column] = {
            "null_count": int(series.isna().sum()),
            "non_null_count": len(present),
            "minimum": _number(min(present.tolist()))
            if numeric and len(present)
            else None,
            "maximum": _number(max(present.tolist()))
            if numeric and len(present)
            else None,
        }
    return summaries


def _number(value: object) -> int | float:
    if isinstance(value, Decimal):
        return float(value)
    item = getattr(value, "item", None)
    return cast("int | float", item() if callable(item) else value)


def trim_inspection(payload: dict[str, Any]) -> bool:
    """Omit the next optional inspection section, preserving output identifiers.

    Returns:
        Whether an optional section was removed.
    """
    truncation = payload["truncation"]
    omitted = truncation["omitted_sections"]
    for output in reversed(list(payload.get("outputs", {}).values())):
        if output["preview"] is not None:
            preview = output["preview"]
            rows = preview if isinstance(preview, list) else preview["features"]
            output["truncation"]["omitted_rows"] += len(rows)
            output["truncation"]["omitted_sections"].append("preview")
            output["preview"] = None
            return True
        if output["metadata"] is not None:
            output["metadata"] = None
            output["truncation"]["omitted_sections"].append("metadata")
            return True
    provenance = payload.get("provenance")
    if provenance and provenance.get("input") is not None:
        provenance["input"] = None
        omitted.append("provenance.input")
        return True
    if payload.get("error") is not None:
        payload["error"] = None
        omitted.append("error")
        return True
    return False
