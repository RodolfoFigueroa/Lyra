"""Executable contracts for city locations and named result tables."""

import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry

from lyra.contracts.units import Unit


def column(dtype: str, description: str, unit: Unit | None = None) -> pa.Column:
    """Declare a described column with an explicit unit.

    Returns:
        A column shared by this example's Pandera declarations.
    """
    return pa.Column(dtype, description=description, metadata={"unit": unit})


def location_schema(attributes: dict[str, pa.Column]) -> pa.DataFrameSchema:
    """Return a strict projected schema for a provider's complete records."""
    return pa.DataFrameSchema(
        {
            "location_id": pa.Column(
                str,
                unique=True,
                checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
                description="Record identifier",
                metadata={"unit": None},
            ),
            "geometry": pa.Column(
                Geometry(crs="EPSG:32614"),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
                description="Projected geometry",
                metadata={"unit": None},
            ),
            **attributes,
        },
        strict=True,
    )


STREET_SCHEMA = location_schema(
    {
        "condition_score": column(
            "float64", "Synthetic condition: 0 is worst, 100 is best", Unit.SCORE
        ),
    }
)
BLOCK_SCHEMA = location_schema(
    {
        "street_records": column("str", "Complete member street records as JSON"),
        "allocations": column("str", "Street allocation weights as JSON"),
    }
)
STREETS = "city_streets"
BLOCKS = "city_blocks"

# docs:start lengths
LENGTHS = pa.DataFrameSchema(
    {
        "street_id": column("str", "Street identifier"),
        "length_m": column("float64", "Centreline length in metres", Unit.METRE),
    },
    strict=True,
    metadata={"meaning": "Street lengths", "granularity": "One row per street"},
)
# docs:end lengths

CONDITIONS = pa.DataFrameSchema(
    {
        "street_id": column("str", "Street identifier"),
        "condition_score": column(
            "float64", "Synthetic condition: 0 is worst, 100 is best", Unit.SCORE
        ),
    },
    strict=True,
)
MAINTENANCE = pa.DataFrameSchema(
    LENGTHS.columns
    | CONDITIONS.columns
    | {
        "needs_maintenance": column("bool", "Condition is below the chosen threshold"),
        "maintenance_length_m": column(
            "float64",
            "Full street length if maintenance is needed, otherwise zero",
            Unit.METRE,
        ),
    },
    strict=True,
    metadata={"meaning": "Maintenance workload", "granularity": "One row per street"},
)
MEMBERSHIP = pa.DataFrameSchema(
    {
        "block_id": column("str", "Receiving block identifier"),
        "street_id": column("str", "Member street identifier"),
        "weight": column(
            "float64", "Share of street length allocated to this block", Unit.RATIO
        ),
    },
    strict=True,
)
SUMMARY = pa.DataFrameSchema(
    {
        "block_id": column("str", "Block identifier"),
        "allocated_length_m": column("float64", "Weighted street length", Unit.METRE),
        "maintenance_length_m": column(
            "float64", "Weighted maintenance workload", Unit.METRE
        ),
    },
    strict=True,
    metadata={
        "meaning": "Allocated maintenance workload",
        "granularity": "One row per block",
    },
)
