"""Scoped location fixtures for handler and execution tests."""

from typing import TypeVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
from lyra.sdk import LocationOutputSchema, Locations
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import locations_from_snapshot
from lyra.sdk.models.location_payload import LocationPayload
from lyra.sdk.output_schema import OutputSchema
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import shape

from tests.location_data import columns

T = TypeVar("T")


def scoped_payload(value: T) -> T | dict:
    """Give geometry fixtures a default test scope."""
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    payload.pop("type", None)
    return {"collection": "test:units", "unit_type": "lot", **payload}


def output_schema(**properties: object) -> LocationOutputSchema:
    """Build a strict location schema for test provider properties."""
    return LocationOutputSchema(
        pandera.DataFrameSchema(
            {
                name: pandera.Column(dtype, description=name, metadata={"unit": None})
                for name, dtype in {
                    "id": str,
                    "geometry": Geometry,
                    **properties,
                }.items()
            }
        )
    )


def producer_result(
    value: Locations, schema: LocationOutputSchema | OutputSchema | None = None
) -> gpd.GeoDataFrame:
    """Export an existing test fixture as a native producer frame."""
    contract = (
        schema
        if isinstance(schema, LocationOutputSchema)
        else LocationOutputSchema(schema)
        if schema is not None
        else output_schema()
    )
    frame = value.to_geodataframe(
        id_column=contract.id_column, geometry_column=contract.geometry_column
    )
    for name, column in contract.schema.columns.items():
        if name == contract.geometry_column:
            continue
        if name not in frame and frame.empty:
            frame[name] = pd.Series([], dtype=object)
        if name in frame and not (column.coerce or contract.schema.coerce):
            frame[name] = column.dtype.coerce(frame[name])
    frame = frame[
        [name for name in contract.schema.columns if name in frame]
        + [name for name in frame if name not in contract.schema.columns]
    ]
    return frame


def stored_locations(value: object) -> Locations:
    """Build a fixture at the stored-buffer boundary for snapshot/reader tests."""
    payload = LocationPayload.model_validate(value)
    return locations_from_snapshot(LocationSnapshot(*columns(payload.model_dump())))


def fixture_locations(
    value: dict, schema: LocationOutputSchema | None = None
) -> Locations:
    """Construct execution fixtures through a known producer declaration."""
    contract = schema if schema is not None else output_schema()
    rows = [
        row.model_dump() if hasattr(row, "model_dump") else row
        for row in value["features"]
    ]
    frame = contract.empty_frame(value["crs"]["properties"]["name"])
    if rows:
        frame = gpd.GeoDataFrame(
            [{contract.id_column: row["id"], **row["properties"]} for row in rows],
            geometry=[shape(row["geometry"]) for row in rows],
            crs=value["crs"]["properties"]["name"],
        )
        for name, column in contract.schema.columns.items():
            if name != contract.geometry_column and name in frame:
                frame[name] = column.dtype.coerce(frame[name])
    return contract.build(
        frame, collection=value["collection"], unit_type=value["unit_type"]
    )


def frame_snapshot(
    frame: gpd.GeoDataFrame, *, collection: str, unit_type: str, id_column: str
) -> LocationSnapshot:
    """Construct test frames with an explicit dtype declaration."""
    schema = pandera.DataFrameSchema(
        {
            name: pandera.Column(series.dtype, nullable=True)
            for name, series in frame.items()
        }
    )
    return LocationSnapshot.from_locations(
        frame, schema, collection=collection, unit_type=unit_type, id_column=id_column
    )
