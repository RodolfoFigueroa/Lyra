"""Concrete location contract shared by smoke metrics."""

import pandera.pandas as pa
from lyra.sdk import LocationContract, LocationFamily, LocationOutputSchema

SCHEMA = LocationOutputSchema(
    pa.DataFrameSchema(
        {
            name: pa.Column(dtype, description=name, metadata={"unit": None})
            for name, dtype in {"id": str, "geometry": "geometry"}.items()
        }
    )
)
SUBJECT = LocationContract(
    family=LocationFamily.from_schema("test:units", "lot", SCHEMA),
    source="fixture",
    geographical_edition="current",
    meaning="Provider locations",
)
