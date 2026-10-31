"""Deterministic street and block graphs over a tiny in-memory city."""

from itertools import starmap
from typing import ClassVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from lyra.sdk import (
    Dependency,
    Input,
    IOConfig,
    LocationCollection,
    LocationContext,
    LocationDependency,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    PluginDefinition,
    Unit,
    UnresolvedLocationIDsError,
    location_operation,
    metric,
)
from shapely.geometry import LineString, Point, box

CRS = "EPSG:3857"


def column(dtype: str, description: str, unit: Unit | None = None) -> pa.Column:
    """Declare a described column with explicit unit metadata.

    Returns:
        A Pandera column shared by the example contracts.
    """
    return pa.Column(dtype, description=description, metadata={"unit": unit})


SHAPES = LocationOutputSchema(
    pa.DataFrameSchema(
        {"id": column("str", "Entity ID"), "geometry": column("geometry", "Shape")}
    )
)
RELATIONSHIPS = pa.DataFrameSchema(
    {
        "block_id": column("str", "Block ID"),
        "street_id": column("str", "Street ID"),
        "weight": column("float64", "Share attributed to this block"),
    }
)
SCORES = pa.DataFrameSchema(
    {"id": column("str", "Result entity ID"), "score": column("float64", "Score")}
)
AREAS = pa.DataFrameSchema(
    {
        "id": column("str", "Buffer ID"),
        "area_m2": column("float64", "Projected buffer area", Unit.SQUARE_METRE),
    }
)
SITES = pa.DataFrameSchema(
    {
        "id": column("str", "Site ID"),
        "x": column("float64", "Projected easting", Unit.METRE),
        "y": column("float64", "Projected northing", Unit.METRE),
    }
)


class CityConfig(IOConfig):
    """Configuration for provider-owned preparation in projected metres."""

    origin: float = 0.0


class CityProvider(LocationProvider[CityConfig]):
    """Prepare authoritative entities inside the managed resource lifecycle."""

    config_model = CityConfig
    collections = (
        LocationCollection(
            name="city:entities", unit_types=("block", "street"), crs=CRS
        ),
    )
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        ("city:entities", unit): SHAPES for unit in ("block", "street")
    }

    def setup(self) -> None:
        """Prepare small configured collections without executing a graph."""
        x = self.config.origin
        self.frames = {
            "block": gpd.GeoDataFrame(
                {"id": ["b1", "b2", "b-empty"]},
                geometry=[
                    box(x, 0, x + 10, 10),
                    box(x + 10, 0, x + 20, 10),
                    box(x + 30, 0, x + 40, 10),
                ],
                crs=CRS,
            ),
            "street": gpd.GeoDataFrame(
                {"id": ["s1", "s2", "s3"]},
                geometry=[LineString([(x + i, 0), (x + i, 10)]) for i in (2, 8, 18)],
                crs=CRS,
            ),
        }

    def resolve(
        self, collection: str, unit_type: str, ids: list[str]
    ) -> gpd.GeoDataFrame:
        """Resolve exact IDs from the prepared collection.

        Returns:
            Authoritative geometry in requested order.

        Raises:
            UnresolvedLocationIDsError: If the collection lacks an ID.
        """
        frame = self.frames[unit_type].set_index("id", drop=False)
        missing = [identifier for identifier in ids if identifier not in frame.index]
        if missing:
            raise UnresolvedLocationIDsError(collection, unit_type, missing)
        return frame.loc[ids]


@metric(
    name="street_membership",
    description="Ordinary weighted membership table.",
    outputs={"membership": RELATIONSHIPS},
)
def membership() -> pd.DataFrame:
    """Return memberships, including a street shared by two blocks."""
    return pd.DataFrame(
        {
            "block_id": ["b1", "b1", "b2", "b2"],
            "street_id": ["s1", "s2", "s2", "s3"],
            "weight": [1.0, 0.5, 0.5, 1.0],
        }
    )


@location_operation(
    name="select_streets",
    description="Resolve streets belonging to selected blocks.",
    output=SHAPES,
    inputs={"members": Dependency("membership")},
    collection="city:entities",
    unit_type="street",
)
def select_streets(
    blocks: Locations, members: pd.DataFrame, context: LocationContext
) -> gpd.GeoDataFrame:
    """Discover IDs from membership and preserve their provider identity.

    Returns:
        Resolved streets, including a schema-valid empty selection.
    """
    ids = members.loc[members.block_id.isin(blocks.ids), "street_id"].tolist()
    resolved = context.resolve_locations("city:entities", "street", ids)
    return resolved.to_geodataframe().astype({"id": str})


@metric(
    name="street_score",
    description="Score result streets with a separate supporting street context.",
    outputs={"street_scores": SCORES},
)
def street_score(streets: Locations, surroundings: Locations) -> pd.DataFrame:
    """Return one row per result street; broader context changes only scores."""
    return pd.DataFrame(
        {
            "id": pd.Series(streets.ids, dtype="str"),
            "score": pd.Series(
                [float(len(surroundings.ids))] * len(streets.ids), dtype="float64"
            ),
        }
    )


SELECTED_STREETS = LocationDependency(
    "select_streets", locations={"blocks": Input("blocks")}
)


@metric(
    name="block_score",
    description="Aggregate derived street scores using explicit membership weights.",
    inputs={
        "scores": Dependency(
            "street_scores",
            locations={"streets": SELECTED_STREETS, "surroundings": SELECTED_STREETS},
        ),
        "members": Dependency("membership"),
    },
    outputs={"block_scores": SCORES},
)
def block_score(
    blocks: Locations, scores: pd.DataFrame, members: pd.DataFrame
) -> pd.DataFrame:
    """Return each requested block, including zero for blocks without streets."""
    joined = members.merge(scores, left_on="street_id", right_on="id")
    totals = (joined.score * joined.weight).groupby(joined.block_id).sum()
    return pd.DataFrame(
        {
            "id": pd.Series(blocks.ids, dtype="str"),
            "score": pd.Series(
                [float(totals.get(identifier, 0.0)) for identifier in blocks.ids],
                dtype="float64",
            ),
        }
    )


@metric(
    name="site_source",
    description="Coordinates owned by an ordinary table producer.",
    outputs={"site_coordinates": SITES},
)
def site_source() -> pd.DataFrame:
    """Return a tiny source table for explicit location conversion."""
    return pd.DataFrame({"id": ["site-a", "site-b"], "x": [0.0, 10.0], "y": [0.0, 0.0]})


@location_operation(
    name="sites_from_table",
    description="Explicitly convert coordinate rows into internal locations.",
    output=SHAPES,
    inputs={"rows": Dependency("site_coordinates")},
    collection="analysis:sites",
    unit_type="site",
)
def sites_from_table(rows: pd.DataFrame) -> gpd.GeoDataFrame:
    """Return internal site entities derived from a table dependency."""
    return gpd.GeoDataFrame(
        rows[["id"]].copy(),
        geometry=list(starmap(Point, zip(rows.x, rows.y, strict=True))),
        crs=CRS,
    )


@location_operation(
    name="site_buffers",
    description="Construct ten-metre buffers in a distinct entity scope.",
    output=SHAPES,
    collection="analysis:buffers",
    unit_type="buffer",
)
def site_buffers(sites: Locations) -> gpd.GeoDataFrame:
    """Return new buffer entities with explicit projected geometry arithmetic."""
    frame = sites.to_geodataframe().astype({"id": str}).to_crs(CRS)
    frame.geometry = frame.geometry.buffer(10)
    return frame


@metric(
    name="buffer_area",
    description="Measure supplied buffers in square metres.",
    outputs={"buffer_areas": AREAS},
)
def buffer_area(buffers: Locations) -> pd.DataFrame:
    """Return areas under the supplied buffer IDs."""
    frame = buffers.to_geodataframe().to_crs(CRS)
    return pd.DataFrame({"id": frame.id, "area_m2": frame.area})


@metric(
    name="site_coverage",
    description="Construct internal sites and buffers through declared graph edges.",
    inputs={
        "areas": Dependency(
            "buffer_areas",
            locations={
                "buffers": LocationDependency(
                    "site_buffers",
                    locations={"sites": LocationDependency("sites_from_table")},
                )
            },
        )
    },
    outputs={"coverage": AREAS},
)
def site_coverage(areas: pd.DataFrame) -> pd.DataFrame:
    """Return the computed buffer areas."""
    return areas


def create_plugin() -> PluginDefinition:
    """Return the executable location example and its in-memory provider."""
    return PluginDefinition(
        metrics=[
            membership,
            street_score,
            block_score,
            site_source,
            buffer_area,
            site_coverage,
        ],
        location_operations=[select_streets, sites_from_table, site_buffers],
        resources={"city": CityProvider},
        location_provider="city",
    )
