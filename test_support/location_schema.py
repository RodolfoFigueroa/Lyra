"""Reusable location schema fixtures."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.api.pandas.model_config import BaseConfig
from pandera.engines.geopandas_engine import Geometry
from pandera.typing import Series
from pandera.typing.geopandas import GeoSeries

from lyra.contracts.locations import LocationContract
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.output_schema import OutputSchema
from test_support.location_helpers import (
    stored_locations,
)


def column(
    dtype: object,
    *,
    checks: pa.Check | None = None,
    nullable: bool = False,
    unique: bool = False,
    coerce: bool = False,
) -> pa.Column:
    if dtype is Geometry:
        dtype = Geometry(crs=4326)
    if isinstance(dtype, Geometry) and checks is None:
        checks = pa.Check(lambda s: s.is_valid, name="valid_geometry")
    return pa.Column(
        dtype,
        description="Column",
        metadata={"unit": None},
        checks=checks,
        nullable=nullable,
        unique=unique,
        coerce=coerce,
    )


def declaration(**properties: pa.Column) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"key": column(str), "geometry": column(Geometry), **properties},
        strict=True,
        ordered=True,
    )


def locations(*properties: dict) -> gpd.GeoDataFrame:
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

    return value.rename(columns={"id": "key"})


def contract(schema: OutputSchema) -> LocationOutputSchema:
    return LocationOutputSchema(schema, id_column="key", geometry_column="geometry")


class StrictConfig(BaseConfig):
    """Require complete column contracts."""

    strict = True


class Places(pa.DataFrameModel):
    Config: type[BaseConfig] = StrictConfig

    key: Series[str] = pa.Field(description="Column", metadata={"unit": None})
    geometry: GeoSeries = pa.Field(
        description="Column", metadata={"unit": None}, dtype_kwargs={"crs": 4326}
    )

    @pa.check("geometry", name="valid_geometry")
    @classmethod
    def valid_geometry(cls, series: GeoSeries) -> pd.Series:
        return series.is_valid

    count: Series[pd.Int64Dtype] = pa.Field(
        description="Column", metadata={"unit": None}, nullable=True
    )


def dataset(**changes: object) -> LocationContract:
    structure = contract(declaration(count=column(int, checks=pa.Check.ge(0)))).metadata
    return LocationContract.model_validate(
        {
            "structure": structure,
            "provider_scope": {"provider_id": "example:places"},
            "meaning": "Population survey",
            "observation_period": "2020",
            "release": "final",
            "field_definitions": {"count": "Resident population"},
            **changes,
        }
    )
