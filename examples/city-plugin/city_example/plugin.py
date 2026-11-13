"""Register the complete city graph with explicit or canonical location selection."""

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from city_example.metrics import (
    block_maintenance,
    street_conditions,
    street_lengths,
    street_maintenance,
    street_membership,
)
from city_example.provider import CityBlockProvider, CityStreetProvider
from city_example.schemas import BLOCKS, STREETS
from lyra.sdk import LocationDependency, PluginDefinition, location_operation


def build_plugin(*, canonical: bool) -> PluginDefinition:
    """Build a complete graph for the explicit or canonical tutorial checkpoint.

    Returns:
        Registered metrics, selector, and one managed location provider.
    """

    # docs:start selector
    @location_operation(
        code_revision="1",
        name="city_streets_for_blocks",
        canonical=canonical,
        description="Select each street belonging to any supplied block once.",
        inputs={"blocks": LocationDependency(provider=BLOCKS)},
        target=STREETS,
    )
    def streets_for_blocks(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Expand the complete member street observations supplied by blocks.

        Returns:
            A deterministic union without acquiring another data source.
        """
        records = {
            entry["id"]: entry
            for values in blocks.street_records
            for entry in json.loads(values)
        }
        ordered = [records[key] for key in sorted(records)]
        return gpd.GeoDataFrame(
            {
                "location_id": pd.Series(
                    [entry["id"] for entry in ordered], dtype="str"
                ),
                "condition_score": pd.Series(
                    [entry["condition_score"] for entry in ordered], dtype="float64"
                ),
            },
            geometry=[LineString(entry["coordinates"]) for entry in ordered],
            crs="EPSG:32614",
        )

    # docs:end selector

    return PluginDefinition(
        metrics=[
            street_lengths,
            street_conditions,
            street_maintenance,
            street_membership,
            block_maintenance,
        ],
        location_operations=[streets_for_blocks],
        resources={
            "city_streets": CityStreetProvider,
            "city_blocks": CityBlockProvider,
        },
    )


def create_plugin() -> PluginDefinition:
    """Return the complete plugin with canonical block-to-street selection."""
    return build_plugin(canonical=True)
