"""Route caller-owned block selections to a street dependency's required provider."""

import sys

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa

from lyra.runtime.local import LocalRunner
from lyra.sdk import Dependency, Input, LocationDependency, PluginDefinition, metric

from .capabilities import SELECT_STREETS
from .graph import BLOCKS, STREETS, BlockProvider, StreetProvider, column
from .hybrid import AreaParameters, large_blocks

LENGTHS = pa.DataFrameSchema(
    {
        "location_id": column("str", "Owning street", None),
        "row_key": column("str", "Stable measurement key", None),
        "length_m": column("float64", "Projected street length", "m"),
    },
    strict=True,
    unique=["location_id", "row_key"],
)
SUMMARY = pa.DataFrameSchema(
    {
        "all_length_m": column("float64", "Length from all selected blocks", "m"),
        "large_length_m": column("float64", "Length from qualifying blocks", "m"),
    },
    strict=True,
)


@metric(
    name="street_lengths",
    code_revision="1",
    description="Length of each distinct represented street in metres.",
    inputs={"streets": LocationDependency(provider=STREETS, primary=True)},
    outputs={"lengths": LENGTHS},
)
def street_lengths(streets: gpd.GeoDataFrame) -> pd.DataFrame:
    """Measure records once even when several blocks select the same street.

    Returns:
        Street-owned rows with stable measurement keys.
    """
    records = streets.drop_duplicates("location_id")
    return pd.DataFrame(
        {
            "location_id": records.location_id,
            "row_key": pd.Series("length", index=records.index, dtype="str"),
            "length_m": records.geometry.length,
        }
    )


# Both references read the summary caller's inputs and effective parameters.
# Each explicit operation targets blocks; the receiving metric requires streets.
selected_blocks = LocationDependency(
    operation="large_blocks",
    parameters={"minimum_area": Input("parameters.minimum_area")},
).bind(
    sources=LocationDependency(
        operation="large_blocks", parameters={"minimum_area": 0.0}
    ).bind(sources=Input("blocks"))
)


@metric(
    name="routed_street_summary",
    code_revision="1",
    description="Street lengths for all selected blocks and for large blocks.",
    inputs={
        "blocks": LocationDependency(provider=BLOCKS),
        "all_streets": Dependency("lengths", producer="street_lengths").bind(
            streets=Input("blocks")
        ),
        "large_streets": Dependency("lengths", producer="street_lengths").bind(
            streets=selected_blocks
        ),
    },
    outputs={"summary": SUMMARY},
)
def routed_street_summary(
    blocks: gpd.GeoDataFrame,
    all_streets: pd.DataFrame,
    large_streets: pd.DataFrame,
    *,
    parameters: AreaParameters,
) -> pd.DataFrame:
    """Summarize two bound uses with distinct selections and complete context.

    Returns:
        One unpartitioned row, including zero lengths for empty selections.
    """
    del blocks, parameters
    return pd.DataFrame(
        {
            "all_length_m": [float(all_streets.length_m.sum())],
            "large_length_m": [float(large_streets.length_m.sum())],
        }
    )


def create_plugin() -> PluginDefinition:
    """Register the example using in-memory providers.

    Returns:
        Metrics, the explicit block filter, and the canonical block/street route.
    """
    return PluginDefinition(
        metrics=[street_lengths, routed_street_summary],
        location_operations=[large_blocks, SELECT_STREETS],
        resources={"blocks": BlockProvider, "streets": StreetProvider},
    )


if __name__ == "__main__":
    runner = LocalRunner.from_definitions({"receiving": create_plugin()})
    result = runner.run(
        "routed_street_summary",
        inputs={"blocks": {"provider": BLOCKS, "ids": ["b1", "b2", "b-empty"]}},
        parameters={"minimum_area": 101.0},
    )
    sys.stdout.write(f"{result.tables['summary']}\n{result.metadata}\n")
