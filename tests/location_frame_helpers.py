from datetime import date
from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from geopandas import GeoDataFrame
from pandera.engines.geopandas_engine import Geometry
from pandera.engines.pandas_engine import Date
from shapely.geometry import Point

from lyra.data.location_frames import validate_frame
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.location_validation import validate_snapshot
from lyra.sdk.storage_schema import prepare
from test_support.location_helpers import snapshot_frame


def convert(frame: gpd.GeoDataFrame) -> GeoDataFrame:
    validate_frame(frame, "key")
    if any(type(value) is not str or not value for value in frame["key"]):
        message = "Feature id must be a nonempty string"
        raise ValueError(message)
    if frame.geometry.isna().any():
        message = "geometry cannot be null"
        raise ValueError(message)
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
    schema = pa.DataFrameSchema(
        {
            name: pa.Column(
                dtype,
                nullable=name not in {"key", geometry},
                coerce=name not in {"key", geometry},
                description=name,
                metadata={"unit": None},
            )
            for name, dtype in types.items()
        },
        strict=True,
    )
    prepared = prepare(frame, schema)
    return snapshot_frame(
        validate_snapshot(
            LocationSnapshot.from_locations(
                cast("gpd.GeoDataFrame", prepared.frame),
                prepared.schema,
                columns=prepared.columns,
                collection="example:stations",
                unit_type="sensor",
                id_column="key",
            )
        )
    )


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
