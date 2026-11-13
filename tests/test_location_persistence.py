"""Ordinary readers preserve native standalone tables independently."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_standard_parquet_readers(tmp_path: Path, state: str, *, spatial: bool) -> None:
    frame = pd.DataFrame(
        {"id": ["a", "b"], "count": pd.Series([2**60 + 1, None], dtype="Int64")}
    )
    if state == "all-null":
        frame["count"] = pd.Series([None, None], dtype="Int64")
    if spatial:
        frame = gpd.GeoDataFrame(frame, geometry=[Point(1, 2), Point(3, 4)], crs=3857)
    if state == "empty":
        frame = frame.iloc[:0]
    path = tmp_path / "table.parquet"
    frame.to_parquet(path, index=False)
    read = gpd.read_parquet if spatial else pd.read_parquet
    first, second = read(path), read(path)
    pd.testing.assert_frame_equal(first, frame)
    first.attrs["provider"] = "forged"
    if len(first):
        first.loc[0, "count"] = 0
    pd.testing.assert_frame_equal(second, frame)
    pd.testing.assert_frame_equal(read(path), frame)
