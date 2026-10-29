"""Scoped location fixtures for handler and execution tests."""

from typing import TypeVar

import pandera.pandas as pa
from lyra.sdk import LocationOutputSchema
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
