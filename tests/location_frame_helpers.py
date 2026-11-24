"""Native frames and registered location schemas for ownership tests."""

import geopandas as gpd
import pandas as pd
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from test_support.location_schema import column, contract, declaration


def convert(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if "active" in frame:
        properties = {
            "count": column("Int64", nullable=True),
            "active": column("boolean", nullable=True),
            "ratio": column("Float64", nullable=True),
            "label": column("string", nullable=True),
        }
        crs = 27700
    else:
        properties = (
            {"mixed": column("Int64", nullable=True)} if "mixed" in frame else {}
        )
        crs = 4326
    schema = declaration(**properties)
    schema.columns["geometry"] = column(Geometry(crs=crs))
    return contract(schema).prepare_frame(frame)


def source_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "key": ["002", "001"],
            "count": pd.Series([2**60 + 1, None], dtype="Int64"),
            "active": pd.Series([True, None], dtype="boolean"),
            "ratio": pd.Series([1.5, None], dtype="Float64"),
            "label": pd.Series(["station", None], dtype="string"),
        },
        geometry=[Point(0, 1), Point(2, 3)],
        crs="EPSG:27700",
    )
