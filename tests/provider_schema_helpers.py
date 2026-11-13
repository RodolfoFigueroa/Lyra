import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from test_support.location_schema import column


def declaration() -> pa.DataFrameSchema:
    identifier = column(
        str, checks=pa.Check(lambda s: s.str.len().gt(0), name="has_characters")
    )
    identifier.unique = True
    return pa.DataFrameSchema(
        {
            "location_id": identifier,
            "geometry": column(
                Geometry(crs="EPSG:4326"),
                checks=pa.Check(lambda s: s.is_valid & ~s.is_empty, name="usable"),
            ),
            "count": column("Int64", nullable=True),
        },
        strict=True,
    )


def frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "location_id": pd.Series(["a", "b"], dtype="str"),
            "count": pd.Series([1, None], dtype="Int64"),
        },
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
