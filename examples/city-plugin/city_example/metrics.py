"""Table-producing metrics for street maintenance and block reporting."""

import json

import geopandas as gpd
import pandas as pd
from pydantic import Field

from city_example.calculations import calculate_lengths, calculate_maintenance
from city_example.schemas import (
    BLOCKS,
    CONDITIONS,
    LENGTHS,
    MAINTENANCE,
    MEMBERSHIP,
    STREETS,
    SUMMARY,
)
from lyra.sdk import Dependency, Input, LocationDependency, MetricParameters, metric


# docs:start parameters
class MaintenanceParameters(MetricParameters):
    """Caller-controlled maintenance policy for the demonstration."""

    condition_threshold: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Maintain streets whose condition score is below this value.",
    )


# docs:end parameters


# docs:start lengths
@metric(
    code_revision="1",
    name="city_street_lengths",
    description="Centreline length in metres for each selected street.",
    inputs={"streets": LocationDependency(provider=STREETS)},
    outputs={"lengths": LENGTHS},
)
def street_lengths(streets: gpd.GeoDataFrame) -> pd.DataFrame:
    """Measure native street inputs.

    Returns:
        One identified length for every selected street.
    """
    return calculate_lengths(streets)


# docs:end lengths


# docs:start conditions
@metric(
    code_revision="1",
    name="city_street_conditions",
    private=True,
    description="Read the bundled condition score for each selected street.",
    inputs={"streets": LocationDependency(provider=STREETS)},
    outputs={"conditions": CONDITIONS},
)
def street_conditions(streets: gpd.GeoDataFrame) -> pd.DataFrame:
    """Extract condition observations without changing row identities.

    Returns:
        Street identifiers and their synthetic condition observations.
    """
    return pd.DataFrame(
        streets.drop_duplicates("location_id")[["location_id", "condition_score"]]
    ).rename(columns={"location_id": "street_id"})


# docs:end conditions


# docs:start maintenance
@metric(
    code_revision="1",
    name="city_street_maintenance",
    description="Street maintenance workload under a chosen condition threshold.",
    inputs={
        "streets": LocationDependency(provider=STREETS),
        "lengths": Dependency("lengths", producer="city_street_lengths"),
        "conditions": Dependency("conditions", producer="city_street_conditions"),
    },
    outputs={"maintenance": MAINTENANCE},
)
def street_maintenance(
    streets: gpd.GeoDataFrame,
    parameters: MaintenanceParameters,
    lengths: pd.DataFrame,
    conditions: pd.DataFrame,
) -> pd.DataFrame:
    """Combine identified lengths and conditions.

    Returns:
        All selected streets, including those requiring no maintenance.
    """
    del streets
    return calculate_maintenance(lengths, conditions, parameters.condition_threshold)


# docs:end maintenance


# docs:start membership
@metric(
    code_revision="1",
    name="city_street_membership",
    private=True,
    description="Explicit street allocation weights for the selected blocks.",
    inputs={"blocks": LocationDependency(provider=BLOCKS)},
    outputs={"membership": MEMBERSHIP},
)
def street_membership(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    """Select membership rows covered by the block dataset revision.

    Returns:
        Explicit allocations, including half shares for the shared street.
    """
    rows = [entry for values in blocks.allocations for entry in json.loads(values)]
    return pd.DataFrame(rows, columns=["block_id", "street_id", "weight"]).astype(
        {"block_id": "str", "street_id": "str", "weight": "float64"}
    )


# docs:end membership


# docs:start bindings
selected_streets = LocationDependency("city_streets_for_blocks").bind(
    blocks=Input("blocks")
)

block_inputs = {
    "blocks": LocationDependency(provider=BLOCKS),
    "maintenance": Dependency(
        "maintenance",
        producer="city_street_maintenance",
    ).bind(streets=selected_streets),
    "membership": Dependency("membership", producer="city_street_membership"),
}
# docs:end bindings


@metric(
    code_revision="1",
    name="city_block_maintenance",
    description="Allocated street lengths and maintenance workload per selected block.",
    inputs=block_inputs,
    outputs={"summary": SUMMARY},
)
def block_maintenance(
    blocks: gpd.GeoDataFrame,
    parameters: MaintenanceParameters,
    maintenance: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate explicitly, including zero rows for blocks without streets.

    Returns:
        One weighted workload summary per selected block, in request order.
    """
    del parameters
    joined = membership.merge(maintenance, on="street_id", validate="many_to_one")
    totals = (
        joined.assign(
            allocated_length_m=joined.length_m * joined.weight,
            maintenance_length_m=joined.maintenance_length_m * joined.weight,
        )
        .groupby("block_id")[["allocated_length_m", "maintenance_length_m"]]
        .sum()
    )
    return (
        totals.reindex(list(blocks.location_id), fill_value=0.0)
        .rename_axis("block_id")
        .reset_index()
    )
