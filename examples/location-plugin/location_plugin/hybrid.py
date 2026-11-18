"""Location ownership, relationship rows, and a parameterized hybrid summary."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    MetricParameters,
    PluginDefinition,
    location_operation,
    metric,
)

from .graph import BLOCKS, BlockProvider, StreetProvider, column, select_streets

AREA = pa.DataFrameSchema(
    {
        "location_id": column("str", "Owning block", None),
        "row_key": column("str", "Stable measurement key within the owner", None),
        "area_m2": column("float64", "Projected area in square metres", "m**2"),
    },
    strict=True,
    unique=["location_id", "row_key"],
)
TOTAL = pa.DataFrameSchema(
    {"value": column("float64", "Total projected area", "m**2")}, strict=True
)
COUNT = pa.DataFrameSchema(
    {"value": column("float64", "Qualifying block count", "count")}, strict=True
)


class AreaParameters(MetricParameters):
    """Selection threshold in projected square metres."""

    minimum_area: float = Field(
        100.0, ge=0, description="Minimum projected area in square metres."
    )


class SummaryParameters(AreaParameters):
    """Summary settings, forwarding only the operation threshold."""

    include_requested: bool = Field(
        default=True, description="Sum all requested areas instead of qualifying areas."
    )


@location_operation(
    name="large_blocks",
    code_revision="1",
    description="Large blocks for projected blocks.",
    inputs={"sources": LocationDependency(provider=BLOCKS)},
    target_provider=BLOCKS,
)
def large_blocks(
    records: gpd.GeoDataFrame, *, parameters: AreaParameters
) -> dict[str, tuple[str, ...]]:
    """Map qualifying records to themselves and complete other sources empty.

    Returns:
        Source IDs mapped to qualifying target IDs or empty tuples.
    """
    return {
        identifier: ((identifier,) if area >= parameters.minimum_area else ())
        for identifier, area in zip(
            records.location_id, records.geometry.area, strict=True
        )
    }


@metric(
    name="block_area",
    code_revision="1",
    description="Block area for projected blocks.",
    inputs={"blocks": LocationDependency(provider=BLOCKS, primary=True)},
    outputs={"area": AREA},
)
def block_area(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    """Measure each represented record once, using a stable owner and row key.

    Returns:
        One area row per represented block.
    """
    records = blocks.drop_duplicates("location_id")
    return pd.DataFrame(
        {
            "location_id": pd.Series(records.location_id, dtype="str"),
            "row_key": pd.Series(
                ["area"] * len(records), index=records.index, dtype="str"
            ),
            "area_m2": records.geometry.area.astype("float64"),
        }
    )


@metric(
    name="area_summary",
    code_revision="1",
    description="Area summary for projected blocks.",
    inputs={
        "blocks": LocationDependency(provider=BLOCKS),
        "selected": LocationDependency(
            operation="large_blocks",
            parameters={"minimum_area": Input("parameters.minimum_area")},
        ).bind(sources=Input("blocks")),
        "areas": Dependency("area", producer="block_area"),
        "selected_areas": Dependency("area", producer="block_area").bind(
            blocks=Input("selected")
        ),
    },
    outputs={"total": TOTAL, "count": COUNT},
)
def area_summary(
    blocks: gpd.GeoDataFrame,
    selected: gpd.GeoDataFrame,
    areas: pd.DataFrame,
    selected_areas: pd.DataFrame,
    *,
    parameters: SummaryParameters,
) -> dict[str, pd.DataFrame]:
    """Summarize complete location outputs with matching and explicit bindings.

    Returns:
        Named area total and qualifying block count tables.
    """
    del blocks, selected
    chosen = areas if parameters.include_requested else selected_areas
    return {
        "total": pd.DataFrame({"value": [float(chosen.area_m2.sum())]}),
        "count": pd.DataFrame({"value": [float(len(selected_areas))]}),
    }


@metric(
    name="street_relationships",
    code_revision="1",
    description="One projected street length per block/street relationship.",
    inputs={
        "blocks": LocationDependency(provider=BLOCKS),
        "streets": LocationDependency(operation="select_streets", primary=True).bind(
            blocks=Input("blocks")
        ),
    },
    outputs={
        "relationships": pa.DataFrameSchema(
            {
                "location_id": column("str", "Owning street", None),
                "row_key": column(
                    "str", "Source block identifying the relationship", None
                ),
                "length_m": column("float64", "Projected street length in metres", "m"),
            },
            strict=True,
            unique=["location_id", "row_key"],
        )
    },
)
def street_relationships(
    blocks: gpd.GeoDataFrame, streets: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Retain each relationship to shared targets.

    Returns:
        Unique owner/key pairs, including two rows for a shared street.
    """
    del blocks
    return pd.DataFrame(
        {
            "location_id": streets.location_id,
            "row_key": streets.source_location_id,
            "length_m": streets.geometry.length,
        }
    )


def create_plugin() -> PluginDefinition:
    """Register the hybrid example using an in-memory projected provider.

    Returns:
        The registered metrics, operations, and in-memory providers.
    """
    return PluginDefinition(
        metrics=[block_area, area_summary, street_relationships],
        location_operations=[large_blocks, select_streets],
        resources={"blocks": BlockProvider, "streets": StreetProvider},
    )
