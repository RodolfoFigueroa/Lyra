from datetime import date

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from pandera.engines.pandas_engine import Date
from shapely.geometry import Point

from lyra.data.locations import Locations
from lyra.sdk.location_schema import LocationOutputSchema


def convert(frame: gpd.GeoDataFrame) -> Locations:
    rich = "shape" in frame
    geometry = "shape" if rich else "geometry"
    types = {"key": str, geometry: Geometry}
    if rich:
        types.update(
            count="Int64",
            active="boolean",
            ratio="Float64",
            label="string",
            category=pd.CategoricalDtype(["a"]),
            day=Date,
            time=pd.DatetimeTZDtype(tz="UTC"),
        )
    for name in ("id", "geometry"):
        if rich and name in frame:
            types[name] = str
    if "mixed" in frame:
        types["mixed"] = "Int64"
    schema = LocationOutputSchema(
        pa.DataFrameSchema(
            {
                name: pa.Column(
                    dtype,
                    nullable=name not in {"key", geometry},
                    coerce=name not in {"key", geometry},
                    description=name,
                    metadata={"unit": None},
                )
                for name, dtype in types.items()
            }
        ),
        id_column="key",
        geometry_column=geometry,
    )
    return schema.build(frame, collection="example:stations", unit_type="sensor")


def source_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "key": ["002", "001"],
            "count": pd.Series([2**60 + 1, None], dtype="Int64"),
            "active": pd.Series([True, None], dtype="boolean"),
            "ratio": pd.Series([1.5, None], dtype="Float64"),
            "label": pd.Series(["station", None], dtype="string"),
            "category": pd.Categorical(["a", None]),
            "day": [date(2026, 10, 29), None],
            "time": [pd.Timestamp("2026-10-29T12:00:00Z"), pd.NaT],
            "shape": [Point(0, 1), Point(2, 3)],
        },
        geometry="shape",
        crs="EPSG:27700",
    )
