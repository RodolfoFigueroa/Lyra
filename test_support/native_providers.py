"""Native provider schemas for in-process fixtures."""

import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry

from lyra.sdk.location_schema import LocationOutputSchema


def native_schema(output: LocationOutputSchema, crs: str) -> pa.DataFrameSchema:
    schema = (
        output.schema.rename_columns({output.id_column: "location_id"})
        if output.id_column != "location_id"
        else output.schema
    )
    schema = schema.update_column(
        "location_id",
        dtype=str,
        unique=True,
        nullable=False,
        checks=pa.Check.str_length(min_value=1),
    )
    schema = schema.update_column(
        output.geometry_column,
        dtype=Geometry(crs=crs),
        nullable=False,
        checks=pa.Check(
            lambda values: values.is_valid & ~values.is_empty, name="valid_geometry"
        ),
    )
    if output.geometry_column != "geometry":
        schema = schema.rename_columns({output.geometry_column: "geometry"})
    schema.strict = True
    return schema
