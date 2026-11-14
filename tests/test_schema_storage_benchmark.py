"""Production cache fidelity for scalar and spatial tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from test_support.relational_workload import read_outputs, write_outputs

if TYPE_CHECKING:
    from pathlib import Path

    import pandera.pandas as pandera

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import shapely
from geopandas.testing import assert_geodataframe_equal

from test_support.workloads import schema
from tests.frame_assertions import assert_logical_equal

KINDS = ("numeric", "mixed", "points", "polygons")


def fixture(kind: str, rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(914)
    data = {f"value_{i}": rng.normal(size=rows) for i in range(16)}
    if kind != "numeric":
        data = {
            "id": pd.Series([f"id-{i:09d}" for i in range(rows)], dtype="string"),
            "count": pd.Series(np.arange(rows), dtype="Int64"),
            "flag": pd.Series(np.arange(rows) % 2 == 0, dtype="boolean"),
            "label": pd.Series(
                [f"label-{i % 1000}" for i in range(rows)], dtype="string"
            ),
            "category": pd.Categorical.from_codes(
                np.arange(rows) % 3,
                categories=pd.Index(["a", "b", "c", "unused"]),
                ordered=True,
            ),
            "time": pd.date_range("2024-01-01", periods=rows, freq="ns", tz="UTC"),
            "date": pd.Series(
                pd.date_range("2024-01-01", periods=rows, freq="s"),
                dtype=pd.ArrowDtype(pa.date32()),
            ),
            "value": rng.normal(size=rows),
        }
    frame = pd.DataFrame(data)
    for column in ("category", "time", "date"):
        if column in frame:
            frame[column] = frame[column].astype("string")
    if kind != "numeric":
        frame.loc[
            frame.index[::7], ["count", "flag", "label", "date", "time", "category"]
        ] = None
    if kind in {"points", "polygons"}:
        geometry = shapely.points(
            rng.uniform(-100, 100, rows), rng.uniform(-50, 50, rows)
        )
        if kind == "polygons":
            geometry = shapely.buffer(geometry, 0.01, quad_segs=32)
        frame = gpd.GeoDataFrame(frame, geometry=geometry, crs=4326)
    return frame


def contract(frame: pd.DataFrame) -> pandera.DataFrameSchema:
    return schema(frame)


def equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    if isinstance(expected, gpd.GeoDataFrame):
        assert isinstance(actual, gpd.GeoDataFrame)
        assert_geodataframe_equal(actual, expected, check_dtype=False)
        np.testing.assert_array_equal(
            shapely.to_wkb(actual.geometry), shapely.to_wkb(expected.geometry)
        )
    else:
        assert_logical_equal(actual, expected)
    for name in expected.select_dtypes(include=np.floating):
        np.testing.assert_array_equal(
            np.signbit(actual[name].dropna().to_numpy(dtype=float)),
            np.signbit(expected[name].dropna().to_numpy(dtype=float)),
        )


@pytest.mark.parametrize("kind", KINDS)
def test_populated_roundtrip(tmp_path: Path, kind: str) -> None:
    frame = fixture(kind, 23)
    write(frame, tmp_path)
    restored = read(tmp_path)
    equal(restored, frame)
    contract(frame).validate(restored)


def test_numeric_boundaries(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "signed": pd.Series([-(2**63), 2**63 - 1, None], dtype="Int64"),
            "unsigned": pd.Series([0, 2**64 - 1, None], dtype="string"),
            "zero": pd.Series([-0.0, 0.0, None], dtype="Float64"),
        }
    )
    write(frame, tmp_path)
    equal(read(tmp_path), frame)


def write(frame: pd.DataFrame, path: Path) -> None:
    write_outputs(path, {"result": frame})


def read(path: Path) -> pd.DataFrame:
    return read_outputs(path)["result"]
