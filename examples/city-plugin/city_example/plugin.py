"""Register the complete city graph with explicit or canonical location selection."""

import geopandas as gpd
import pandas as pd

from city_example.metrics import (
    block_maintenance,
    street_conditions,
    street_lengths,
    street_maintenance,
    street_membership,
)
from city_example.provider import CityLocationProvider
from city_example.schemas import BLOCKS, STREET_SCHEMA, STREETS
from lyra.data import Locations
from lyra.sdk import Dependency, LocationContext, PluginDefinition, location_operation


def build_plugin(*, canonical: bool) -> PluginDefinition:
    """Build a complete graph for the explicit or canonical tutorial checkpoint.

    Returns:
        Registered metrics, selector, and one managed location provider.
    """

    # docs:start selector
    @location_operation(
        name="city_streets_for_blocks",
        canonical=canonical,
        description="Select each street belonging to any supplied block once.",
        locations={"blocks": BLOCKS},
        inputs={
            "membership": Dependency("membership", producer="city_street_membership")
        },
        output=STREET_SCHEMA,
        output_contract=STREETS,
    )
    def streets_for_blocks(
        blocks: Locations, membership: pd.DataFrame, context: LocationContext
    ) -> gpd.GeoDataFrame:
        """Resolve member streets through the execution's shared provider.

        Returns:
            A deterministic union of complete street observations.
        """
        del blocks
        ids = list(dict.fromkeys(membership.street_id))
        return context.resolve_locations(
            "example:city", "street", ids
        ).to_geodataframe()

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
        resources={"city_data": CityLocationProvider},
        location_provider="city_data",
    )


def create_plugin() -> PluginDefinition:
    """Return the complete plugin with canonical block-to-street selection."""
    return build_plugin(canonical=True)
