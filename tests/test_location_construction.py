"""Explicit producer contracts and independent stored artifact authority."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from test_support.location_helpers import output_schema


def frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["b", "a"], "count": pd.Series([1, None], dtype="Int64")},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
        index=pd.Index([0, 1], name="ignored"),
    )


def test_declared_types_retained_without_mutation() -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    schema = type(schema)(schema.schema)
    source = frame()[["geometry", "count", "id"]]
    original = source.copy(deep=True)
    value = schema.prepare_frame(source)
    pd.testing.assert_frame_equal(source, original)
    assert value["count"].dtype == pd.Int64Dtype()
    assert value.iloc[:0].dtypes.equals(value.dtypes)
    assert value.loc[value.id.isin(["a"])].dtypes.equals(value.dtypes)
    source.loc[0, "count"] = 99
    assert value["count"].iloc[0] == 1
    assert pd.isna(value["count"].iloc[1])


@pytest.mark.parametrize("empty", [False, True])
def test_exact_columns_required_even_when_empty(*, empty: bool) -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    schema = type(schema)(schema.schema)
    source = frame().iloc[:0] if empty else frame()
    for invalid in [source.drop(columns="count"), source.assign(extra=1)]:
        with pytest.raises(ValueError, match="declared columns"):
            schema.prepare_frame(invalid)
