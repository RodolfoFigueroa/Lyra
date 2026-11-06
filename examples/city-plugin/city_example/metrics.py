"""Table-producing metrics for street maintenance and block reporting."""

import pandas as pd
from pydantic import Field

from city_example.calculations import calculate_lengths, calculate_maintenance
from city_example.data import load_city
from city_example.schemas import (
    BLOCKS,
    CONDITIONS,
    LENGTHS,
    MAINTENANCE,
    MEMBERSHIP,
    STREETS,
    SUMMARY,
)
from lyra.data import Locations
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
    name="city_street_lengths",
    description="Centreline length in metres for each selected street.",
    locations={"streets": STREETS},
    outputs={"lengths": LENGTHS},
)
def street_lengths(streets: Locations) -> pd.DataFrame:
    """Measure native street inputs.

    Returns:
        One identified length for every selected street.
    """
    return calculate_lengths(streets.to_geodataframe())


# docs:end lengths


# docs:start conditions
@metric(
    name="city_street_conditions",
    private=True,
    description="Read the bundled condition score for each selected street.",
    locations={"streets": STREETS},
    outputs={"conditions": CONDITIONS},
)
def street_conditions(streets: Locations) -> pd.DataFrame:
    """Extract condition observations without changing row identities.

    Returns:
        Street identifiers and their synthetic condition observations.
    """
    return streets.to_dataframe().rename(columns={"id": "street_id"})


# docs:end conditions


# docs:start maintenance
@metric(
    name="city_street_maintenance",
    description="Street maintenance workload under a chosen condition threshold.",
    locations={"streets": STREETS},
    inputs={
        "lengths": Dependency("lengths", producer="city_street_lengths"),
        "conditions": Dependency("conditions", producer="city_street_conditions"),
    },
    outputs={"maintenance": MAINTENANCE},
)
def street_maintenance(
    streets: Locations,
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
    name="city_street_membership",
    private=True,
    description="Explicit street allocation weights for the selected blocks.",
    locations={"blocks": BLOCKS},
    outputs={"membership": MEMBERSHIP},
)
def street_membership(blocks: Locations) -> pd.DataFrame:
    """Select membership rows covered by the block dataset revision.

    Returns:
        Explicit allocations, including half shares for the shared street.
    """
    rows = pd.DataFrame([entry.model_dump() for entry in load_city().membership])
    return rows.loc[rows.block_id.isin(blocks.ids)].reset_index(drop=True)


# docs:end membership


# docs:start bindings
selected_streets = LocationDependency(
    "city_streets_for_blocks", locations={"blocks": Input("blocks")}
)

block_inputs = {
    "maintenance": Dependency(
        "maintenance",
        producer="city_street_maintenance",
        locations={"streets": selected_streets},
    ),
    "membership": Dependency("membership", producer="city_street_membership"),
}
# docs:end bindings


@metric(
    name="city_block_maintenance",
    description="Allocated street lengths and maintenance workload per selected block.",
    locations={"blocks": BLOCKS},
    inputs=block_inputs,
    outputs={"summary": SUMMARY},
)
def block_maintenance(
    blocks: Locations,
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
        totals.reindex(list(blocks.ids), fill_value=0.0)
        .rename_axis("block_id")
        .reset_index()
    )
