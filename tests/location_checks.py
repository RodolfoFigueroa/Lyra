"""Typed geometry fixtures shared by storage and graph tests."""

import pandera.pandas as pa

from lyra.sdk.location_schema import LocationOutputSchema
from test_support.location_data import payload
from tests.location_graph import schema


def rich_payload(rows: int) -> dict:
    """Return schema-representable mixed geometry and typed property inputs."""
    raw = payload(rows)
    geometries = [f["geometry"] for f in payload(7)["features"]]
    for index, feature in enumerate(raw["features"]):
        feature["geometry"] = geometries[index % len(geometries)]
        feature["properties"] = {
            "label": f"place-{index}",
            "count": 2**60 + index,
            "ratio": index / 8,
            "active": bool(index % 2),
        }
    return raw


def rich_schema() -> LocationOutputSchema:
    """Return the typed property contract for the rich workload."""
    output = schema()
    for name, dtype in {
        "label": str,
        "count": int,
        "ratio": float,
        "active": bool,
    }.items():
        output.schema = output.schema.add_columns(
            {name: pa.Column(dtype, description=name, metadata={"unit": None})}
        )
    # Recompile metadata as well as the schema used by the graph.
    return type(output)(output.schema)
