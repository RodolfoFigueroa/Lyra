"""Edition factories and canonical selection over the shared in-memory city."""

import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd

from lyra.data import Locations
from lyra.sdk import (
    Dependency,
    LocationContext,
    PluginDefinition,
    location_operation,
    metric,
)

from .graph import (
    BLOCKS,
    RELATIONSHIPS,
    SCORES,
    SHAPES,
    STREETS,
    CityProvider,
    membership,
)

# This revision covers block geometry and block-to-street membership.
BLOCK_DATA = BLOCKS.model_copy(update={"data_revision": "city-correction-1"})
# Street observations are unversioned; release and observation period describe meaning.
STREET_DATA = STREETS.model_copy(
    update={
        "observation_period": "2025",
        "release": "city-survey",
        "data_revision": None,
    }
)


class CapabilityProvider(CityProvider):
    """Reuse the city fixtures under the exact declared data contracts."""

    contracts = (BLOCK_DATA, STREET_DATA)


@metric(
    name="membership",
    private=True,
    description="Weighted city membership used by selectors.",
    locations={"blocks": BLOCK_DATA},
    outputs={"rows": RELATIONSHIPS},
)
def relationships(blocks: Locations) -> pd.DataFrame:
    """Return membership covered by the selected blocks' declared revision."""
    rows = membership()
    return rows.loc[rows.block_id.isin(blocks.ids)]


def selector(name: str) -> Callable[..., object]:
    """Build a batched canonical selector with fully bound dependencies.

    Returns:
        An operation selecting a deterministic union of complete street rows.
    """

    @location_operation(
        name=name,
        canonical=True,
        description="Select streets belonging to any supplied block.",
        locations={"blocks": BLOCK_DATA},
        inputs={"members": Dependency("rows", producer="membership")},
        output=SHAPES,
        output_contract=STREET_DATA,
    )
    def select(
        blocks: Locations, members: pd.DataFrame, context: LocationContext
    ) -> gpd.GeoDataFrame:
        ids = sorted(set(members.loc[members.block_id.isin(blocks.ids), "street_id"]))
        return (
            context.resolve_locations("city:entities", "street", ids)
            .to_geodataframe()
            .astype({"id": str})
        )

    return select


SELECT_STREETS = selector("member_streets")
ALTERNATE_STREETS = selector("alternate_member_streets")
STREET_SCORES = SCORES.update_columns({})
STREET_SCORES.metadata = {
    "meaning": "Context street count multiplied by the edition's fixed factor",
    "granularity": "One row per selected street",
}


def score_edition(edition: str, factor: float) -> tuple[Callable[..., object], ...]:
    """Build a concrete private calculation and public edition from one method.

    Returns:
        The public metric and its private calculation dependency.
    """
    producer = f"context_score_{edition}"
    locations = {"streets": STREET_DATA, "surroundings": STREET_DATA}

    @metric(
        name=producer,
        private=True,
        description="Score streets using the separately selected context.",
        locations=locations,
        outputs={"scores": STREET_SCORES},
    )
    def calculate(streets: Locations, surroundings: Locations) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "id": pd.Series(streets.ids, dtype="str"),
                "score": pd.Series(
                    [factor * len(surroundings.ids)] * len(streets.ids), dtype="float64"
                ),
            }
        )

    @metric(
        name=f"street_rating_{edition}",
        family="street_rating",
        edition=edition,
        edition_metadata={"factor": factor, "method": "Context count times factor"},
        description="Street ratings using an independently selected street context.",
        locations=locations,
        inputs={"scores": Dependency("scores", producer=producer)},
        outputs={"scores": STREET_SCORES},
    )
    def rating(
        streets: Locations, surroundings: Locations, scores: pd.DataFrame
    ) -> pd.DataFrame:
        # The concrete dependency receives these native inputs by name.
        del streets, surroundings
        return scores

    return rating, calculate


@metric(
    name="block_count",
    description="Count native blocks; no canonical route targets blocks.",
    locations={"blocks": BLOCK_DATA},
    outputs={"scores": SCORES},
)
def block_count(blocks: Locations) -> pd.DataFrame:
    """Return one count row per block without an edition selector."""
    return pd.DataFrame({"id": blocks.ids, "score": [1.0] * len(blocks.ids)})


def create_plugin() -> PluginDefinition:
    """Return the capability example with an explicit shortest-route preference."""
    return PluginDefinition(
        metrics=[
            relationships,
            block_count,
            *score_edition("standard", 1.0),
            *score_edition("double", 2.0),
        ],
        location_operations=[SELECT_STREETS, ALTERNATE_STREETS],
        route_preferences=[(SELECT_STREETS,)],
        resources={"city": CapabilityProvider},
        location_provider="city",
    )


def selection(unit: str, *ids: str) -> dict[str, object]:
    """Return a public ID selection, leaving concrete bindings to preparation."""
    return {"collection": "city:entities", "unit_type": unit, "ids": list(ids)}


def main() -> None:
    """Run native tables and inspect their separate provenance without services."""
    from lyra.runtime.local import LocalRunner  # ruff: ignore[import-outside-top-level]

    with TemporaryDirectory() as directory:
        runner = LocalRunner.from_definitions(
            {"city": create_plugin()}, artifact_dir=Path(directory)
        )
        result = runner.run(
            "street_rating",
            {
                "streets": selection("block", "b1", "b2"),
                "surroundings": selection("street", "s1"),
            },
            edition="double",
            cache_revision="external-refresh-1",
        )
        sys.stdout.write(f"{result['scores']}\n")
        provenance = runner.inspect()
        if provenance is not None:
            sys.stdout.write(f"Native inputs: {provenance.execution.native_inputs}\n")


if __name__ == "__main__":
    main()
