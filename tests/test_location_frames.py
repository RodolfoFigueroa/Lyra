from datetime import UTC, datetime, timedelta
from decimal import Decimal

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import GeometryCollection, Point, shape

from lyra.data.scalars import normalize_property
from lyra.sdk.location_schema import LocationOutputSchema
from tests.geometry_fixtures import VALUE_GEOMETRIES as GEOMETRIES
from tests.location_frame_helpers import convert, source_frame


def test_order_properties_crs_and_custom_columns() -> None:
    frame = source_frame()
    frame.index = [90, -2]
    value = convert(frame)
    assert value["key"].tolist() == ["002", "001"]
    assert value["count"].iloc[0] == 2**60 + 1
    assert value.drop(columns=["key", "shape"]).iloc[1].isna().all()
    result = value.copy(deep=True)
    assert result.crs == frame.crs
    assert result.active_geometry_name == "shape"
    assert list(result.columns) == [
        "key",
        "shape",
        *[name for name in frame.columns if name not in {"key", "shape"}],
    ]
    assert result.index.tolist() == [0, 1]
    pd.testing.assert_frame_equal(convert(result), value)
    assert value.copy(deep=True)["key"].tolist() == value["key"].tolist()


@pytest.mark.parametrize(("kind", "coordinates"), GEOMETRIES.items())
def test_supported_geometries(kind: str, coordinates: list) -> None:
    geometry = shape({"type": kind, "coordinates": coordinates})
    frame = gpd.GeoDataFrame({"key": ["x"]}, geometry=[geometry], crs=4326)
    value = convert(frame)
    assert value.copy(deep=True).geometry.iloc[0].equals_exact(geometry, 0)
    assert value.geometry.iloc[0].geom_type == kind


def test_both_conversion_directions_are_independent() -> None:
    frame = source_frame()
    value = convert(frame)
    original = value.to_dict()
    frame.loc[0, "key"] = "changed"
    frame.loc[0, "count"] = 99
    frame["shape"] = gpd.GeoSeries([Point(9, 9), Point(2, 3)], crs=frame.crs)
    result = value.copy(deep=True)
    result.loc[0, "id"] = "changed"
    result.loc[0, "count"] = 98
    result["geometry"] = gpd.GeoSeries([Point(8, 8), Point(2, 3)], crs=result.crs)
    assert value.to_dict() == original
    assert value.copy(deep=True).loc[0, "count"] == 2**60 + 1


def test_empty_conversion() -> None:
    value = convert(source_frame().iloc[:0])
    assert value["key"].tolist() == []
    result = value.copy(deep=True)
    assert result.empty
    assert list(result.columns) == [
        "key",
        "shape",
        "count",
        "active",
        "ratio",
        "label",
        "category",
        "day",
        "time",
    ]
    assert result.crs == source_frame().crs
    pd.testing.assert_frame_equal(convert(result), value)


@pytest.mark.parametrize("identifier", [None, pd.NA, np.nan, 1, True, b"id", ""])
def test_invalid_ids_are_not_normalized(identifier: object) -> None:
    frame = gpd.GeoDataFrame({"key": [identifier]}, geometry=[Point(0, 0)], crs=4326)
    with pytest.raises(ValueError, match="id"):
        convert(frame)


def test_duplicate_ids() -> None:
    frame = source_frame()
    frame["key"] = ["same", "same"]
    with pytest.raises(ValueError, match="unique"):
        convert(frame)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        b"bytes",
        complex(1, 2),
        timedelta(days=1),
        np.timedelta64(1, "s"),
        np.timedelta64(1, "ns"),
        np.timedelta64(1, "ps"),
        Decimal("1.2"),
        float("inf"),
        Point(0, 0),
    ],
)
def test_unsupported_properties_identify_column(value: object) -> None:
    frame = source_frame()
    frame["extra"] = pd.Series([value, None], dtype=object)
    with pytest.raises(ValueError, match="extra"):
        normalize_property(value, "extra")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (np.int64(3), 3),
        (np.float32(1.5), 1.5),
        (np.bool_(1), True),
        (np.str_("a"), "a"),
        (np.datetime64("2026-10-29"), "2026-10-29T00:00:00"),
        (np.datetime64("NaT"), None),
        (np.timedelta64("NaT"), None),
        (datetime(2026, 10, 29, tzinfo=UTC), "2026-10-29T00:00:00+00:00"),
        (np.float64("nan"), None),
    ],
)
def test_object_column_scalars(value: object, expected: object) -> None:
    frame = source_frame()
    frame["extra"] = pd.Series([value, None], dtype=object)
    assert normalize_property(value, "extra") == expected


def test_invalid_frame_contracts() -> None:
    frame = source_frame()
    with pytest.raises(ValueError, match="unique"):
        convert(pd.concat([frame, frame[["key"]]], axis=1))
    with pytest.raises(ValueError, match="Missing ID"):
        convert(frame.drop(columns="key"))
    with pytest.raises(ValueError, match="geometry"):
        LocationOutputSchema(
            pa.DataFrameSchema(
                {
                    "shape": pa.Column(
                        Geometry, description="Shape", metadata={"unit": None}
                    )
                },
                strict=True,
            ),
            id_column="shape",
            geometry_column="shape",
        )
    without_crs = gpd.GeoDataFrame({"key": ["x"]}, geometry=[Point(0, 0)])
    with pytest.raises(ValueError, match="CRS"):
        convert(without_crs)
    with pytest.raises(ValueError, match="active geometry"):
        convert(gpd.GeoDataFrame({"key": []}))
    with pytest.raises(ValueError, match="strings"):
        convert(frame.rename(columns={"count": 123}))


@pytest.mark.parametrize(
    "geometry", [None, GeometryCollection(), Point(float("inf"), 0), Point()]
)
def test_invalid_geometry_is_not_filtered(geometry: object) -> None:
    frame = gpd.GeoDataFrame(
        {"key": ["x"]}, geometry=gpd.GeoSeries([geometry]), crs=4326
    )
    with pytest.raises((TypeError, ValueError)):
        convert(frame)


def test_sparse_object_properties_are_independent() -> None:
    frame = gpd.GeoDataFrame(
        {
            "key": ["a", "b", "c"],
            "mixed": pd.Series([pd.NA, None, 2**60], dtype="Int64"),
        },
        geometry=[Point(0, 0)] * 3,
        crs=4326,
    )
    value = convert(frame)
    expected = value.copy(deep=True)
    assert value["mixed"].iloc[:2].isna().all()
    exported = value.copy(deep=True)
    pd.testing.assert_frame_equal(convert(exported), expected)
    source_array = frame["mixed"].to_numpy()
    source_array.flags.writeable = True
    source_array[2] = 0
    exported_array = exported["mixed"].to_numpy()
    exported_array.flags.writeable = True
    exported_array[2] = 1
    pd.testing.assert_frame_equal(value, expected)
