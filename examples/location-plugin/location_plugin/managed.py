"""Managed city collections and computational tree correspondence callbacks."""

from typing import Annotated, cast

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from pydantic import Field

from lyra.sdk import (
    LocationDependency,
    MetricParameters,
    Origin,
    ParquetLocationProvider,
    PluginDefinition,
    PostGISLocationProvider,
    PostgresIO,
    Resource,
    ResourceConfig,
    SecretReference,
    location_operation,
)

CITY = Origin(id="city-inventory", revision="2026")


def record_schema(crs: str, attributes: dict[str, pa.Column]) -> pa.DataFrameSchema:
    """Return a complete schema, including strict IDs and valid geometry."""
    return pa.DataFrameSchema(
        {
            "location_id": pa.Column(
                str,
                unique=True,
                checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
                description="Stable record identifier",
                metadata={"unit": None},
            ),
            "geometry": pa.Column(
                Geometry(crs=crs),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
                description="Native geometry",
                metadata={"unit": None},
            ),
            **attributes,
        },
        strict=True,
    )


class CityBlocks(PostGISLocationProvider):
    """Read an existing city relation through the shared database resource."""

    database: Annotated[PostgresIO, Resource("city_db")]
    id = "managed_blocks"
    code_revision = "1"
    origins = (CITY,)
    schema = record_schema(
        "EPSG:4326",
        {
            "tree_id": pa.Column(
                str, description="Assigned tree", metadata={"unit": None}
            )
        },
    )
    db_schema = "public"
    table = "blocks"


class Trees(ParquetLocationProvider):
    """Read one configured file with projected tree geometry."""

    id = "managed_trees"
    code_revision = "1"
    origins = (CITY,)
    schema = record_schema(
        "EPSG:32614",
        {"height": pa.Column(float, description="Tree height", metadata={"unit": "m"})},
    )


class TreeParameters(MetricParameters):
    """Thresholds measured in the target collection's metre units."""

    minimum_height: float = Field(5.0, ge=0, description="Exclusive height threshold")
    maximum_distance: float = Field(100.0, ge=0, description="Inclusive distance limit")


def select_all_trees(_sources: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
    """Return the complete symbolic target relation."""
    return relation


def select_tall_trees(_sources: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
    """Return complete records with height above five metres."""
    return relation.filter(relation.height > 5)


def select_nearby_trees(
    sources: gpd.GeoDataFrame, trees: ir.Table, *, parameters: TreeParameters
) -> ir.Table:
    """Return a sufficient candidate pool for all supplied blocks."""
    if sources.empty:
        return trees.filter([False])
    projected = sources.to_crs("EPSG:32614")
    area = ibis.literal(projected.geometry.union_all(), type=dt.GeoSpatial(srid=32614))
    return trees.filter(
        trees.height > parameters.minimum_height,
        trees.geometry.d_within(area, distance=parameters.maximum_distance),
    )


@location_operation(
    name="nearby_trees",
    code_revision="1",
    description="Map blocks to mature trees within the requested distance.",
    inputs={"blocks": LocationDependency(provider=CityBlocks.id)},
    target_provider=Trees.id,
    target_selector=select_nearby_trees,
)
def nearby_trees(
    records: gpd.GeoDataFrame,
    pool: gpd.GeoDataFrame,
    *,
    parameters: TreeParameters,
) -> dict[str, tuple[str, ...]]:
    """Return correspondences refined independently for each represented block."""
    distinct = records.drop_duplicates("location_id")
    if pool.empty:
        return dict.fromkeys(distinct.location_id, ())
    projected = distinct.to_crs("EPSG:32614")
    return {
        identifier: tuple(
            sorted(
                pool.loc[
                    pool.geometry.distance(geometry) <= parameters.maximum_distance,
                    "location_id",
                ]
            )
        )
        for identifier, geometry in zip(
            projected.location_id, projected.geometry, strict=True
        )
    }


@location_operation(
    name="assigned_trees",
    code_revision="1",
    description="Resolve tree IDs already recorded on blocks.",
    inputs={"blocks": LocationDependency(provider=CityBlocks.id)},
    target_provider=Trees.id,
)
def assigned_trees(records: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
    """Return each distinct block mapped to its recorded tree without a selector."""
    return {
        cast("str", row.location_id): (cast("str", row.tree_id),)
        for row in records.itertuples()
    }


plugin = PluginDefinition(
    location_operations=[nearby_trees, assigned_trees],
    resources={"city_db": PostgresIO, "block_source": CityBlocks, "tree_source": Trees},
)

resource_config = {
    "city_db": ResourceConfig(
        config={
            "host": "db.example.com",
            "port": 5432,
            "database": "city",
            "user": "lyra",
        },
        secrets={"password": SecretReference(env="CITY_DB_PASSWORD")},
    ),
    "tree_source": ResourceConfig(config={"path": "/data/trees.parquet"}),
}
