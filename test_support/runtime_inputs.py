"""Construct private invocation records from existing storage fixtures."""

from collections.abc import Mapping
from typing import Any

import geopandas as gpd
import pandas as pd

from lyra.runtime.native_inputs import NativeLocationInput
from test_support.location_helpers import node_contract


def native_inputs(values: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for name, value in values.items():
        if isinstance(value, gpd.GeoDataFrame):
            result[name] = NativeLocationInput(
                value,
                node_contract("example:places", "point").reference
                if str(value.columns[0]) == "key"
                else node_contract().reference,
                str(value.columns[0]),
            )
    return result


def frame_ids(frame: gpd.GeoDataFrame) -> list[str]:
    return frame[str(frame.columns[0])].tolist()


def assert_records(actual: object, expected: object) -> None:
    """Compare native working data and captured scope where both sides carry it."""

    def frame(value: object) -> pd.DataFrame:
        if isinstance(value, NativeLocationInput):
            return value.frame
        assert isinstance(value, pd.DataFrame)
        return value

    pd.testing.assert_frame_equal(frame(actual), frame(expected))
    if isinstance(actual, NativeLocationInput) and isinstance(
        expected, NativeLocationInput
    ):
        assert actual.contract == expected.contract
