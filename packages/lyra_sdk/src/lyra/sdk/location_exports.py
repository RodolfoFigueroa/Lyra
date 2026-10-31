"""Explicit GIS directory exports with scope metadata and preservation checks."""

import importlib
import json
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
from lyra.sdk import content_fingerprints, geometry_shapely
from lyra.sdk.artifact_reader import LocationArtifact
from lyra.sdk.models.location import Locations, location_snapshot


def _fail(reason: str) -> None:
    raise ValueError(reason)


def _shapefile_column(column: str, values: list, types: set[type]) -> None:
    if len(column) > 10:
        _fail(f"Column {column!r}: shapefile field name exceeds 10 characters")
    if types not in ({str}, {int}) or any(value is None for value in values):
        _fail(f"Column {column!r}: requires non-null strings or integers")
    if types == {int} and any(abs(value) > 2**53 for value in values):
        _fail(f"Column {column!r}: integer exceeds safe shapefile range")
    if types == {str} and any(
        not value or value != value.strip() or len(value.encode("utf-8")) > 254
        for value in values
    ):
        _fail(f"Column {column!r}: unsupported shapefile string")


def _driver_column(
    frame: gpd.GeoDataFrame, column: str, values: list, types: set[type]
) -> None:
    if not column.isascii() or not column.isidentifier():
        _fail(f"Column {column!r}: unsupported field name")
    if column.lower() in {"fid", "geom"}:
        _fail(f"Column {column!r}: reserved driver field name")
    if types == {bool}:
        frame[column] = pd.Series(values, dtype="boolean")


def _driver_frame(frame: gpd.GeoDataFrame) -> None:
    names = [name.lower() for name in frame.columns]
    if len(set(names)) != len(names):
        _fail("Case-insensitive field name collision")
    if len(set(frame.geom_type)) > 1:
        _fail("Mixed geometry families require GeoParquet")


def _prepare(locations: Locations, file_format: str) -> gpd.GeoDataFrame:
    frame = locations.to_geodataframe(id_column="id", geometry_column="geometry")
    for column in frame.columns.drop("geometry"):
        values = [None if pd.isna(value) else value for value in frame[column].tolist()]
        stored_name = (
            location_snapshot(locations).scope.id_column if column == "id" else column
        )
        dtype = location_snapshot(locations).table.schema.field(stored_name).type
        types = (
            {int}
            if pa.types.is_integer(dtype)
            else {str}
            if pa.types.is_string(dtype) or pa.types.is_large_string(dtype)
            else {bool}
            if pa.types.is_boolean(dtype)
            else {float}
            if pa.types.is_floating(dtype)
            else set()
        )
        if types == {int}:
            if any(
                value is not None and not -(2**63) <= value < 2**63 for value in values
            ):
                _fail(f"Column {column!r}: integer outside signed 64-bit range")
            if file_format != "geoparquet":
                frame[column] = pd.Series(values, dtype="Int64")
        if file_format != "geoparquet":
            _driver_column(frame, column, values, types)
        if file_format == "shapefile":
            _shapefile_column(column, values, types)
    if file_format != "geoparquet":
        _driver_frame(frame)
    return frame


def _property_fingerprint(frame: gpd.GeoDataFrame) -> str:
    table = pa.Table.from_pandas(
        pd.DataFrame(frame.drop(columns="geometry")), preserve_index=False
    )
    return content_fingerprints.typed_table(table)


def export_locations(
    source: Locations | LocationArtifact,
    path: str | Path,
    *,
    file_format: str = "geoparquet",
) -> Path:
    """Publish a new directory containing GIS data and scope.json.

    Existing destinations are never overwritten. Scope metadata retains the exact
    collection, unit type and CRS declaration. GIS data retains ordered IDs,
    coordinates and properties; unsupported contents raise ValueError. Optional
    pyogrio drivers are loaded only for GeoPackage and shapefile.

    Returns:
        Path to the published directory.

    Raises:
        FileExistsError: If the destination exists.
        ValueError: If contents or the requested export backend are unsupported.
    """
    if file_format not in {"geoparquet", "geopackage", "shapefile"}:
        _fail(f"Unknown export file_format: {file_format!r}")
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(destination)
    locations = (
        source.to_locations() if isinstance(source, LocationArtifact) else source
    )
    frame = _prepare(locations, file_format)
    driver = {"geopackage": "GPKG", "shapefile": "ESRI Shapefile"}.get(file_format)
    if driver is not None:
        try:
            backend = importlib.import_module("pyogrio")
        except ImportError as error:
            message = f"{file_format} export requires the optional pyogrio backend"
            raise ValueError(message) from error
        if "w" not in backend.list_drivers().get(driver, ""):
            _fail(f"Export requires the optional {driver} write driver")
    suffix = {"geoparquet": "parquet", "geopackage": "gpkg", "shapefile": "shp"}[
        file_format
    ]
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary) / "export"
        staging.mkdir()
        output = staging / f"locations.{suffix}"
        if file_format == "geoparquet":
            frame.to_parquet(output, index=False)
            restored = gpd.read_parquet(output)
        else:
            frame.to_file(output, driver=driver, engine="pyogrio", index=False)
            restored = gpd.read_file(output, engine="pyogrio")
        expected_geometry = location_snapshot(locations).geometry
        actual_geometry = geometry_shapely.ingest_geometries(
            restored.geometry.to_numpy()
        )
        same_properties = _property_fingerprint(restored) == _property_fingerprint(
            frame
        )
        if (
            restored.crs != frame.crs
            or not same_properties
            or content_fingerprints.typed_table(actual_geometry)
            != content_fingerprints.typed_table(expected_geometry)
        ):
            _fail(f"{file_format} cannot preserve geometry or properties exactly")
        metadata = {
            "collection": locations.collection,
            "unit_type": locations.unit_type,
            "crs": locations.crs.model_dump(),
        }
        (staging / "scope.json").write_text(json.dumps(metadata), encoding="utf-8")
        if destination.exists():
            raise FileExistsError(destination)
        staging.rename(destination)
    return destination
