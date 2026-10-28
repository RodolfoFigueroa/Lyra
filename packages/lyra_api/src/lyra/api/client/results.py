"""Result and JSON download policies shared by both clients."""

from typing import Any

import geopandas as gpd
import pandas as pd
from lyra.api.client.endpoints import check_status
from lyra.api.exceptions import DownloadError, MetricRunError
from lyra.sdk.models.job import FailedJobResult, TableJobResult, TerminalJobResult


def successful_result(result: TerminalJobResult) -> TableJobResult:
    """Return successful named outputs or raise the job's failure.

    Raises:
        MetricRunError: If execution failed.
    """
    if isinstance(result, FailedJobResult):
        raise MetricRunError(result)
    return result


def download_needs_text(status: int) -> bool:
    """Return whether an HTTP error needs decoding before writing a file."""
    return status != 200


def validate_download(
    status: int, text: str, retry_after: str | None, *, operation: str
) -> None:
    """Validate HTTP status before opening the destination."""
    check_status(status, text, retry_after, operation=operation)


def decode_tables(
    result: TerminalJobResult,
) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
    """Decode all public outputs, retaining names, order and explicit identifiers.

    Empty columns come from declarations; public JSON does not restore dtypes.

    Returns:
        Native tables with fresh indices and WGS84 spatial coordinates.

    Raises:
        DownloadError: If a named output cannot be decoded.
    """
    result = successful_result(result)
    tables: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {}
    for name, payload in result.outputs.items():
        declaration = result.output_metadata[name]
        columns = [column.name for column in declaration.columns]
        try:
            if isinstance(payload, list):
                tables[name] = (
                    pd.DataFrame(payload) if payload else pd.DataFrame(columns=columns)
                )
            else:
                tables[name] = _spatial_table(
                    payload, columns, declaration.geometry or "geometry"
                )
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            message = f"Failed to decode result tables: invalid output {name!r}."
            raise DownloadError(message) from exc
    return tables


def _spatial_table(
    payload: dict[str, Any], columns: list[str], geometry_name: str
) -> gpd.GeoDataFrame:
    """Construct geometry separately so GeoJSON properties retain their names.

    Returns:
        A spatial table in the public CRS.

    Raises:
        ValueError: If features or their properties have an invalid shape.
    """
    features = payload["features"]
    rows = []
    geometries = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            message = "Expected a GeoJSON Feature"
            raise ValueError(message)
        properties = feature.get("properties")
        if properties is not None and not isinstance(properties, dict):
            message = "Expected GeoJSON properties to be an object or null"
            raise ValueError(message)
        if "geometry" not in feature:
            message = "Expected a GeoJSON geometry"
            raise ValueError(message)
        value = feature["geometry"]
        if value is not None and (
            not isinstance(value, dict) or not isinstance(value.get("type"), str)
        ):
            message = "Expected a geometry object or null"
            raise ValueError(message)
        rows.append(properties or {})
        geometries.append(
            {"type": "Feature", "properties": {}, "geometry": feature["geometry"]}
        )
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    if geometry_name in frame.columns:
        message = "Geometry name conflicts with a property"
        raise ValueError(message)
    try:
        geometry = (
            gpd.GeoDataFrame.from_features(geometries, crs="EPSG:4326").geometry
            if geometries
            else gpd.GeoSeries([], crs="EPSG:4326")
        )
    except Exception as exc:
        # GeoPandas delegates malformed coordinates to several geometry backends.
        message = "Invalid GeoJSON geometry"
        raise ValueError(message) from exc
    frame[geometry_name] = geometry
    return gpd.GeoDataFrame(frame, geometry=geometry_name, crs="EPSG:4326")
