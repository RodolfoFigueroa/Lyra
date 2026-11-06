"""Reusable location schema fixtures."""

import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from pandera.typing import Series
from pandera.typing.geopandas import GeoSeries

from lyra.contracts.locations import LocationContract
from lyra.data.locations import Locations, locations_from_snapshot
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.output_schema import OutputSchema
from test_support.location_helpers import (
    frame_snapshot,
    node_contract,
    stored_locations,
)


def column(
    dtype: object,
    *,
    checks: pa.Check | None = None,
    nullable: bool = False,
    coerce: bool = False,
) -> pa.Column:
    return pa.Column(
        dtype,
        description="Column",
        metadata={"unit": None},
        checks=checks,
        nullable=nullable,
        coerce=coerce,
    )


def declaration(**properties: pa.Column) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"key": column(str), "shape": column(Geometry), **properties}, ordered=True
    )


def locations(*properties: dict) -> Locations:
    value = stored_locations(
        {
            "collection": "example:places",
            "unit_type": "point",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(index),
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                    "properties": values,
                }
                for index, values in enumerate(properties)
            ],
        }
    )

    return locations_from_snapshot(
        frame_snapshot(
            value.to_geodataframe(id_column="key", geometry_column="shape"),
            collection=value.collection,
            unit_type=value.unit_type,
            id_column="key",
        )
    ).with_contract(node_contract("example:places", "point"))


def contract(schema: OutputSchema) -> LocationOutputSchema:
    return LocationOutputSchema(schema, id_column="key", geometry_column="shape")


class Places(pa.DataFrameModel):
    key: Series[str] = pa.Field(description="Column", metadata={"unit": None})
    shape: GeoSeries = pa.Field(description="Column", metadata={"unit": None})
    count: Series[pd.Int64Dtype] = pa.Field(
        description="Column", metadata={"unit": None}, nullable=True
    )


def dataset(**changes: object) -> LocationContract:
    family = contract(declaration(count=column(int, checks=pa.Check.ge(0)))).family(
        "example:places", "point"
    )
    return LocationContract.model_validate(
        {
            "family": family,
            "source": "survey-2020",
            "geographical_edition": "boundaries-2010",
            "meaning": "Population survey",
            "observation_period": "2020",
            "release": "final",
            "field_definitions": {"count": "Resident population"},
            **changes,
        }
    )
