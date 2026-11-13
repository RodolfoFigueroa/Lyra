"""Deterministic provider records and pure location operations for a tiny city."""

import json

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely import from_wkt
from shapely.geometry import LineString, Point, box
from typing_extensions import override

from lyra.contracts.units import Unit
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    location_operation,
    metric,
)

CRS = "EPSG:3857"
BLOCKS = "city_blocks"
STREETS = "city_streets"
SITES_PROVIDER = "analysis_sites"
BUFFERS = "analysis_buffers"


def column(dtype: str, description: str, unit: Unit | None = None) -> pa.Column:
    """Return a described Pandera column with explicit unit metadata."""
    return pa.Column(dtype, description=description, metadata={"unit": unit})


def shape_schema(attributes: dict[str, pa.Column] | None = None) -> pa.DataFrameSchema:
    """Return a strict schema retaining all declared provider attributes."""
    return pa.DataFrameSchema(
        {
            "location_id": pa.Column(
                str,
                unique=True,
                checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
                description="Entity identifier",
                metadata={"unit": None},
            ),
            "geometry": pa.Column(
                Geometry(crs=CRS),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
                description="Projected geometry",
                metadata={"unit": None},
            ),
            **(attributes or {}),
        },
        strict=True,
    )


SHAPES = shape_schema()
BLOCK_SCHEMA = shape_schema(
    {"street_records": column("str", "Complete member streets as JSON")}
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


def streets_frame() -> gpd.GeoDataFrame:
    """Return the fixed complete street collection."""
    return gpd.GeoDataFrame(
        {"location_id": pd.Series(["s1", "s2", "s3"], dtype="str")},
        geometry=[LineString([(x, 0), (x, 10)]) for x in (2, 8, 18)],
        crs=CRS,
    )


class StreetProvider(LocationProvider):
    """Expose the fixed synthetic street collection."""

    id = STREETS
    code_revision = "1"
    schema = SHAPES
    origins = ()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return exact requested street records."""
        rows = streets_frame()
        return rows.loc[rows.location_id.isin(ids)].copy()


class BlockProvider(LocationProvider):
    """Expose block boundaries and their fixed street relationships."""

    id = BLOCKS
    code_revision = "1"
    schema = BLOCK_SCHEMA
    origins = ()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return selected blocks with their complete member street records."""
        streets = streets_frame().set_index("location_id")
        memberships = {"b1": ["s1", "s2"], "b2": ["s2", "s3"], "b-empty": []}
        rows = gpd.GeoDataFrame(
            {
                "location_id": pd.Series(list(memberships), dtype="str"),
                "street_records": pd.Series(
                    [
                        json.dumps(
                            [
                                {
                                    "location_id": key,
                                    "geometry": streets.loc[key].geometry.wkt,
                                }
                                for key in keys
                            ]
                        )
                        for keys in memberships.values()
                    ],
                    dtype="str",
                ),
            },
            geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(30, 0, 40, 10)],
            crs=CRS,
        )
        return rows.loc[rows.location_id.isin(ids)].copy()


class SiteProvider(LocationProvider):
    """Expose fixed sites as provider records."""

    id = SITES_PROVIDER
    code_revision = "1"
    schema = SHAPES
    origins = ()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return selected site point records."""
        rows = gpd.GeoDataFrame(
            {"location_id": pd.Series(["site-a", "site-b"], dtype="str")},
            geometry=[Point(0, 0), Point(10, 0)],
            crs=CRS,
        )
        return rows.loc[rows.location_id.isin(ids)].copy()


class BufferProvider(SiteProvider):
    """Expose the same sites' fixed ten-metre buffer collection."""

    id = BUFFERS

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return the declared buffered records."""
        rows = super().resolve(ids)
        rows.geometry = rows.geometry.buffer(10)
        return rows


def selected_street_records(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the union of complete street records supplied by blocks."""
    records = {
        entry["location_id"]: entry
        for value in blocks.street_records
        for entry in json.loads(value)
    }
    keys = sorted(records)
    return gpd.GeoDataFrame(
        {"location_id": pd.Series(keys, dtype="str")},
        geometry=[from_wkt(records[key]["geometry"]) for key in keys],
        crs=CRS,
    )


@metric(
    code_revision="1",
    name="street_membership",
    description="Ordinary weighted membership table.",
    outputs={"membership": RELATIONSHIPS},
)
def membership() -> pd.DataFrame:
    """Return fixed memberships, including a street shared by two blocks."""
    return pd.DataFrame(
        {
            "block_id": ["b1", "b1", "b2", "b2"],
            "street_id": ["s1", "s2", "s2", "s3"],
            "weight": [1.0, 0.5, 0.5, 1.0],
        }
    )


@location_operation(
    code_revision="1",
    name="select_streets",
    description="Select complete member street records from blocks.",
    inputs={"blocks": LocationDependency(provider=BLOCKS)},
    target=STREETS,
)
def select_streets(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the pure union of each block's supplied street records."""
    return selected_street_records(blocks)


@metric(
    code_revision="1",
    name="street_score",
    inputs={
        "streets": LocationDependency(provider=STREETS),
        "surroundings": LocationDependency(provider=STREETS),
    },
    description="Score streets using separately selected supporting streets.",
    outputs={"street_scores": SCORES},
)
def street_score(
    streets: gpd.GeoDataFrame, surroundings: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Return one row per street, with the supporting context's count."""
    return pd.DataFrame(
        {
            "id": pd.Series(streets.location_id, dtype="str"),
            "score": pd.Series(
                [float(len(surroundings))] * len(streets), dtype="float64"
            ),
        }
    )


SELECTED_STREETS = LocationDependency("select_streets").bind(blocks=Input("blocks"))


@metric(
    code_revision="1",
    name="block_score",
    description="Aggregate derived street scores using explicit membership weights.",
    inputs={
        "blocks": LocationDependency(provider=BLOCKS),
        "scores": Dependency("street_scores", producer="street_score").bind(
            streets=SELECTED_STREETS, surroundings=SELECTED_STREETS
        ),
        "members": Dependency("membership", producer="street_membership"),
    },
    outputs={"block_scores": SCORES},
)
def block_score(
    blocks: gpd.GeoDataFrame, scores: pd.DataFrame, members: pd.DataFrame
) -> pd.DataFrame:
    """Return each selected block, including zero for blocks without streets."""
    joined = members.merge(scores, left_on="street_id", right_on="id")
    totals = (joined.score * joined.weight).groupby(joined.block_id).sum()
    return pd.DataFrame(
        {
            "id": pd.Series(blocks.location_id, dtype="str"),
            "score": pd.Series(
                [float(totals.get(key, 0.0)) for key in blocks.location_id],
                dtype="float64",
            ),
        }
    )


@metric(
    code_revision="1",
    name="site_source",
    description="Fixed source coordinates.",
    outputs={"site_coordinates": SITES},
)
def site_source() -> pd.DataFrame:
    """Return a tiny coordinate table independent of provider acquisition."""
    return pd.DataFrame({"id": ["site-a", "site-b"], "x": [0.0, 10.0], "y": [0.0, 0.0]})


@location_operation(
    code_revision="1",
    name="site_buffers",
    description="Select each site's declared ten-metre buffer.",
    inputs={"sites": LocationDependency(provider=SITES_PROVIDER)},
    target=BUFFERS,
)
def site_buffers(sites: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return buffer records matching the registered buffer provider."""
    result = sites.copy()
    result.geometry = result.geometry.buffer(10)
    return result


@metric(
    code_revision="1",
    name="buffer_area",
    description="Measure declared buffers in square metres.",
    inputs={"buffers": LocationDependency(provider=BUFFERS)},
    outputs={"buffer_areas": AREAS},
)
def buffer_area(buffers: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return the selected buffers' projected areas."""
    return pd.DataFrame({"id": buffers.location_id, "area_m2": buffers.area})


@metric(
    code_revision="1",
    name="site_coverage",
    description="Compute buffer areas through a declared location operation.",
    inputs={
        "sites": LocationDependency(provider=SITES_PROVIDER),
        "areas": Dependency("buffer_areas", producer="buffer_area").bind(
            buffers=LocationDependency("site_buffers").bind(sites=Input("sites"))
        ),
    },
    outputs={"coverage": AREAS},
)
def site_coverage(sites: gpd.GeoDataFrame, areas: pd.DataFrame) -> pd.DataFrame:
    """Return computed buffer areas for the selected sites."""
    del sites
    return areas


def create_plugin() -> PluginDefinition:
    """Return the executable graph and its independent provider registrations."""
    return PluginDefinition(
        metrics=[
            membership,
            street_score,
            block_score,
            site_source,
            buffer_area,
            site_coverage,
        ],
        location_operations=[select_streets, site_buffers],
        resources={
            "blocks": BlockProvider,
            "streets": StreetProvider,
            "sites": SiteProvider,
            "buffers": BufferProvider,
        },
    )
