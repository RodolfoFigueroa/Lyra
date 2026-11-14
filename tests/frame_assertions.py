"""Shared production regression fixtures and assertions."""

from __future__ import annotations

import pandas as pd
import shapely


def assert_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Check exact values, pandas metadata, CRS, and geometry encoding.

    Raises:
        AssertionError: If any native table contract differs.
    """
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    if hasattr(expected, "geometry"):
        if actual.crs != expected.crs:
            msg = "CRS changed"
            raise AssertionError(msg)
        left = shapely.to_wkb(actual.geometry.to_numpy())
        right = shapely.to_wkb(expected.geometry.to_numpy())
        if not (left == right).all():
            msg = "Geometry encoding changed"
            raise AssertionError(msg)


def assert_logical_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """Compare supported logical types, values, and spatial metadata."""
    dtypes = {
        "int64": "Int64",
        "float64": "Float64",
        "bool": "boolean",
        "str": "string",
        "string": "string",
        "string[python]": "string",
        "string[pyarrow]": "string",
    }
    normalized = [
        frame.astype(
            {
                name: dtypes[str(dtype)]
                for name, dtype in frame.dtypes.items()
                if str(dtype) in dtypes
            }
        )
        for frame in (actual, expected)
    ]
    assert_equal(*normalized)
