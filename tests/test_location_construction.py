"""Explicit producer contracts and independent stored artifact authority."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas import GeoDataFrame
from shapely.geometry import Point

from test_support.location_helpers import native_snapshot, output_schema


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
    stored = native_snapshot(value).table.schema
    assert stored is not None
    assert stored.names == ["id", "count"]
    assert str(stored.field("count").type) == "int64"
    assert stored.field("count").nullable
    assert native_snapshot(value.iloc[:0]).table.schema == stored
    assert native_snapshot(value.loc[value.id.isin(["a"])]).table.schema == stored
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


@pytest.mark.parametrize("boundary", ["build", "validate"])
@pytest.mark.parametrize("nullable", [False, True])
def test_nullability_after_schema_processing(boundary: str, *, nullable: bool) -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = nullable
    schema = type(schema)(schema.schema)
    source = frame().fillna({"count": 2})
    original = source.copy(deep=True)
    value = schema.prepare_frame(source)

    def introduce_null(result: pd.DataFrame) -> bool:
        result.loc[0, "count"] = None
        return True

    schema.schema.checks = [pa.Check(introduce_null)]

    def construct() -> GeoDataFrame:
        if boundary == "validate":
            return schema.prepare_frame(value)
        return schema.prepare_frame(source)

    if nullable:
        assert pd.isna(construct()["count"].iloc[0])
        assert construct()["count"].iloc[1] == 2
    else:
        with pytest.raises(ValueError, match="not nullable"):
            construct()
    pd.testing.assert_frame_equal(source, original)
    assert value["count"].tolist() == [1, 2]
