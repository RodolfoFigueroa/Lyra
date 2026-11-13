"""Edition factories and canonical selection over the shared in-memory city."""

import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd

from lyra.sdk import (
    Dependency,
    LocationDependency,
    Origin,
    PluginDefinition,
    location_operation,
    metric,
)

from .graph import (
    BLOCKS,
    RELATIONSHIPS,
    SCORES,
    STREETS,
    BlockProvider,
    StreetProvider,
    membership,
    selected_street_records,
)

BLOCK_DATA = BLOCKS
STREET_DATA = STREETS


class CapabilityBlocks(BlockProvider):
    """Expose corrected block relationships under an explicit code revision."""

    code_revision = "city-correction-1"


class CapabilityStreets(StreetProvider):
    """Declare the fixed street survey used by this capability example."""

    origins = (Origin(id="city-survey", revision="2025"),)


@metric(
    code_revision="1",
    name="membership",
    private=True,
    description="Weighted city membership used by selectors.",
    inputs={"blocks": LocationDependency(provider=BLOCK_DATA)},
    outputs={"rows": RELATIONSHIPS},
)
def relationships(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return membership covered by the selected blocks' declared revision."""
    rows = membership()
    return rows.loc[rows.block_id.isin(blocks.location_id)]


def selector(name: str) -> Callable[..., object]:
    """Build a batched canonical selector with fully bound dependencies.

    Returns:
        An operation selecting a deterministic union of complete street rows.
    """

    @location_operation(
        code_revision="1",
        name=name,
        canonical=True,
        description="Select streets belonging to any supplied block.",
        inputs={"blocks": LocationDependency(provider=BLOCK_DATA)},
        target=STREET_DATA,
    )
    def select(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return selected_street_records(blocks)

    return select


SELECT_STREETS = selector("member_streets")
ALTERNATE_STREETS = selector("alternate_member_streets")
STREET_SCORES = SCORES.update_columns({})
STREET_SCORES.metadata = {
    "meaning": "Context street count multiplied by the variant's fixed factor",
    "granularity": "One row per selected street",
}


def score_variant(variant: str, factor: float) -> tuple[Callable[..., object], ...]:
    """Build a concrete private calculation and public variant from one method.

    Returns:
        The public metric and its private calculation dependency.
    """
    producer = f"context_score_{variant}"
    locations = {
        "streets": LocationDependency(provider=STREET_DATA),
        "surroundings": LocationDependency(provider=STREET_DATA),
    }

    @metric(
        code_revision="1",
        name=producer,
        private=True,
        description="Score streets using the separately selected context.",
        inputs=locations,
        outputs={"scores": STREET_SCORES},
    )
    def calculate(
        streets: gpd.GeoDataFrame, surroundings: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "id": pd.Series(streets.location_id, dtype="str"),
                "score": pd.Series(
                    [factor * len(surroundings.location_id)] * len(streets.location_id),
                    dtype="float64",
                ),
            }
        )

    @metric(
        code_revision="1",
        name=f"street_rating_{variant}",
        description="Street ratings using an independently selected street context.",
        inputs=locations | {"scores": Dependency("scores", producer=producer)},
        outputs={"scores": STREET_SCORES},
    )
    def rating(
        streets: gpd.GeoDataFrame, surroundings: gpd.GeoDataFrame, scores: pd.DataFrame
    ) -> pd.DataFrame:
        # The concrete dependency receives these native inputs by name.
        del streets, surroundings
        return scores

    return rating, calculate


@metric(
    code_revision="1",
    name="block_count",
    description="Count native blocks; no canonical route targets blocks.",
    inputs={"blocks": LocationDependency(provider=BLOCK_DATA)},
    outputs={"scores": SCORES},
)
def block_count(blocks: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return one count row per block for selected native blocks."""
    return pd.DataFrame(
        {"id": blocks.location_id, "score": [1.0] * len(blocks.location_id)}
    )


def create_plugin() -> PluginDefinition:
    """Return the capability example with an explicit shortest-route preference."""
    return PluginDefinition(
        metrics=[
            relationships,
            block_count,
            *score_variant("standard", 1.0),
            *score_variant("double", 2.0),
        ],
        location_operations=[SELECT_STREETS, ALTERNATE_STREETS],
        route_preferences=[(SELECT_STREETS,)],
        resources={"blocks": CapabilityBlocks, "streets": CapabilityStreets},
    )


def selection(unit: str, *ids: str) -> dict[str, object]:
    """Return a public ID selection, leaving concrete bindings to preparation."""
    return {
        "provider": {"block": BLOCK_DATA, "street": STREET_DATA}[unit],
        "ids": list(ids),
    }


def main() -> None:
    """Run native tables and inspect their separate provenance without services."""
    from lyra.runtime.local import LocalRunner  # ruff: ignore[import-outside-top-level]

    with TemporaryDirectory() as directory:
        runner = LocalRunner.from_definitions(
            {"city": create_plugin()}, artifact_dir=Path(directory)
        )
        result = runner.run(
            "street_rating_double",
            inputs={
                "streets": selection("block", "b1", "b2"),
                "surroundings": selection("street", "s1"),
            },
            cache_revision="external-refresh-1",
        )
        sys.stdout.write(f"{result['scores']}\n")
        provenance = runner.inspect()
        if provenance is not None:
            sys.stdout.write(f"Native inputs: {provenance.execution.native_inputs}\n")


if __name__ == "__main__":
    main()
