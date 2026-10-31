"""Scoped location fixtures for handler and execution tests."""

from typing import TypeVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from lyra.sdk import LocationOutputSchema, Locations
from lyra.sdk.output_schema import OutputSchema
from pandera.engines.geopandas_engine import Geometry

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
        pa.DataFrameSchema(
            {
                name: pa.Column(dtype, description=name, metadata={"unit": None})
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
