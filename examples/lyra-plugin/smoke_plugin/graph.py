"""Population graphs over illustrative plugin-owned source views.

The example views are not platform tables. Deployments supply their own sources.
"""

from typing import Annotated

import pandas as pd
import pandera.pandas as pa
from lyra.sdk import (
    DatabaseIO,
    Dependency,
    Input,
    LocationInput,
    MetricParameters,
    Resource,
    Unit,
    metric,
)
from pydantic import Field

POPULATION = pa.DataFrameSchema(
    {
        "area_code": pa.Column(
            "str", description="Source area identifier.", metadata={"unit": None}
        ),
        "population": pa.Column(
            "int64", description="Resident count.", metadata={"unit": Unit.COUNT}
        ),
    }
)
SPATIAL_POPULATION = pa.DataFrameSchema(
    POPULATION.columns
    | {
        "geometry": pa.Column(
            "geometry", description="Geometry", metadata={"unit": None}
        )
    }
)
TOTAL = pa.DataFrameSchema(
    {
        "population": pa.Column(
            "int64",
            description="Total residents in selected source areas.",
            metadata={"unit": Unit.COUNT},
        )
    }
)


class Year(MetricParameters):
    """Source observation year."""

    year: int = Field(default=2020, description="Census observation year.")


class Baseline(MetricParameters):
    """Public baseline selection."""

    baseline_year: int = Field(
        default=2020, description="Census year used for the baseline."
    )


@metric(
    name="example_city_source",
    description="Read the plugin's fixed-city census view.",
    outputs={"example_city_records": POPULATION},
)
def city_source(
    parameters: Year,
    database: Annotated[DatabaseIO, Resource("database")],
) -> pd.DataFrame:
    """Acquire fixed-region records without public spatial inputs.

    Returns:
        Source areas and resident counts.
    """
    return database.query_dataframe(
        "SELECT area_code, population FROM example_city_census "
        "WHERE year = :year ORDER BY area_code",
        params={"year": parameters.year},
    )


@metric(
    name="example_city_population",
    description="Population of the plugin's fixed city by source area.",
    inputs={"records": Dependency("example_city_records")},
    outputs={"example_city_population": POPULATION},
)
def city_population(parameters: Year, records: pd.DataFrame) -> pd.DataFrame:
    """Forward year to the source and publish its table.

    Returns:
        The supplied source records.
    """
    del parameters
    return records


@metric(
    name="example_selected_source",
    description="Read census areas intersecting a client-selected region.",
    outputs={"example_selected_records": SPATIAL_POPULATION},
)
def selected_source(
    location: LocationInput,
    parameters: Year,
    database: Annotated[DatabaseIO, Resource("database")],
) -> pd.DataFrame:
    """Acquire spatial records; the source stores WGS84 geometries.

    The SQL transforms request geometries using their declared CRS. Explicit
    result CRS metadata also covers empty query results.

    Returns:
        Intersecting source areas with their geometries.
    """
    return database.query_geodataframe(
        """
        SELECT c.area_code, c.population, c.geometry
        FROM example_spatial_census AS c
        WHERE c.year = :year AND EXISTS (
            SELECT 1 FROM
            jsonb_array_elements(CAST(:selection AS jsonb)->'features') AS f
            WHERE ST_Intersects(c.geometry, ST_Transform(
                ST_GeomFromGeoJSON(f->'geometry'), :source_crs, 4326))
        ) ORDER BY c.area_code
        """,
        params={
            "year": parameters.year,
            "selection": location.model_dump_json(),
            "source_crs": location.crs.properties.name,
        },
        geom_col="geometry",
        crs="EPSG:4326",
    )


@metric(
    name="example_population_baseline",
    description=(
        "Census population by intersecting source area and total; "
        "counts include entire intersecting areas."
    ),
    inputs={
        "records": Dependency(
            "example_selected_records",
            arguments={"parameters": {"year": Input("parameters.baseline_year")}},
        )
    },
    outputs={
        "example_population_areas": SPATIAL_POPULATION,
        "example_population_total": TOTAL,
    },
)
def population_baseline(
    location: LocationInput, parameters: Baseline, records: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Return source geometries and an explicitly calculated aggregate."""
    del location, parameters
    areas = records.copy()
    return {
        "example_population_areas": areas,
        "example_population_total": pd.DataFrame(
            {"population": [int(records["population"].sum())]}
        ),
    }
