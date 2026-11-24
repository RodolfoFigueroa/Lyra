"""Bounded native-table summaries and shared inspection truncation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import geopandas as gpd

from lyra.contracts.inspection import MAX_INSPECTION_BYTES, json_size, trim_inspection
from lyra.data.results import serialize_outputs

if TYPE_CHECKING:
    import pandas as pd

    from lyra.contracts.outputs import OutputMetadata
    from lyra.data.results import NativeOutputs


def summarize_outputs(
    outputs: NativeOutputs, declarations: dict[str, OutputMetadata]
) -> dict[str, Any]:
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
                "summary": _summaries(table, columns, declarations[name]),
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


def _summaries(
    table: pd.DataFrame, columns: list[str], declaration: OutputMetadata
) -> dict:
    dtypes = {column.name: column.dtype for column in declaration.columns}
    summaries = {}
    for column in columns:
        series = table[column]
        count = int(series.count())
        dtype = dtypes.get(column)
        minimum = maximum = None
        if count and dtype in {"int64", "Int64"}:
            minimum, maximum = int(series.min()), int(series.max())
        elif count and dtype in {"float64", "Float64"}:
            minimum, maximum = float(series.min()), float(series.max())
        summaries[column] = {
            "null_count": len(series) - count,
            "non_null_count": count,
            "minimum": minimum,
            "maximum": maximum,
        }
    return summaries
