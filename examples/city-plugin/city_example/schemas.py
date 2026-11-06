"""Executable contracts for city locations and named result tables."""

import pandera.pandas as pa

from lyra.contracts.locations import LocationContract
from lyra.contracts.units import Unit
from lyra.sdk import LocationOutputSchema


def column(dtype: str, description: str, unit: Unit | None = None) -> pa.Column:
    """Declare a described column with an explicit unit.

    Returns:
        A column shared by this example's Pandera declarations.
    """
    return pa.Column(dtype, description=description, metadata={"unit": unit})


STREET_SCHEMA = LocationOutputSchema(
    pa.DataFrameSchema(
        {
            "id": column("str", "Street identifier"),
            "geometry": column("geometry", "Projected street centreline"),
            "condition_score": column(
                "float64", "Synthetic condition: 0 is worst, 100 is best", Unit.SCORE
            ),
        }
    )
)
BLOCK_SCHEMA = LocationOutputSchema(
    pa.DataFrameSchema(
        {
            "id": column("str", "Block identifier"),
            "geometry": column("geometry", "Projected block boundary"),
        }
    )
)
STREETS = LocationContract(
    family=STREET_SCHEMA.family("example:city", "street"),
    source="bundled-survey",
    geographical_edition="layout_v1",
    data_revision="fixture_1",
    meaning="Synthetic street geometry and condition observations for Example City",
)
BLOCKS = LocationContract(
    family=BLOCK_SCHEMA.family("example:city", "block"),
    source="bundled-city",
    geographical_edition="layout_v1",
    data_revision="fixture_1",
    meaning="Synthetic block geometry and street membership for Example City",
)

# docs:start lengths
LENGTHS = pa.DataFrameSchema(
    {
        "street_id": column("str", "Street identifier"),
        "length_m": column("float64", "Centreline length in metres", Unit.METRE),
    },
    metadata={"meaning": "Street lengths", "granularity": "One row per street"},
)
# docs:end lengths

CONDITIONS = pa.DataFrameSchema(
    {
        "street_id": column("str", "Street identifier"),
        "condition_score": column(
            "float64", "Synthetic condition: 0 is worst, 100 is best", Unit.SCORE
        ),
    }
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
    metadata={"meaning": "Maintenance workload", "granularity": "One row per street"},
)
MEMBERSHIP = pa.DataFrameSchema(
    {
        "block_id": column("str", "Receiving block identifier"),
        "street_id": column("str", "Member street identifier"),
        "weight": column(
            "float64", "Share of street length allocated to this block", Unit.RATIO
        ),
    }
)
SUMMARY = pa.DataFrameSchema(
    {
        "block_id": column("str", "Block identifier"),
        "allocated_length_m": column("float64", "Weighted street length", Unit.METRE),
        "maintenance_length_m": column(
            "float64", "Weighted maintenance workload", Unit.METRE
        ),
    },
    metadata={
        "meaning": "Allocated maintenance workload",
        "granularity": "One row per block",
    },
)
