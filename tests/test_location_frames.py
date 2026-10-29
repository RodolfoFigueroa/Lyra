from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from lyra.sdk import Locations
from shapely.geometry import GeometryCollection, Point, shape

from tests.test_locations_values import GEOMETRIES


def convert(frame: gpd.GeoDataFrame) -> Locations:
    return Locations.from_geodataframe(
        frame,
        collection="example:stations",
        unit_type="sensor",
        id_column="key",
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


def test_order_properties_crs_and_custom_columns() -> None:
    frame = source_frame()
    frame.index = [90, -2]
    value = convert(frame)
    assert value.ids == ["002", "001"]
    assert value.collection == "example:stations"
    assert value.unit_type == "sensor"
    assert value.features[0].properties == {
        "count": 2**60 + 1,
        "active": True,
        "ratio": 1.5,
        "label": "station",
        "category": "a",
        "day": "2026-10-29",
        "time": "2026-10-29T12:00:00+00:00",
    }
    assert set(value.features[1].properties.values()) == {None}
    result = value.to_geodataframe(id_column="key", geometry_column="shape")
    assert result.crs == frame.crs
    assert result.active_geometry_name == "shape"
    assert list(result.columns) == list(frame.columns)
    assert result.index.tolist() == [0, 1]
    assert convert(result) == value
    assert value.to_geodataframe()["id"].tolist() == value.ids


@pytest.mark.parametrize(("kind", "coordinates"), GEOMETRIES.items())
def test_supported_geometries(kind: str, coordinates: list) -> None:
    geometry = shape({"type": kind, "coordinates": coordinates})
    frame = gpd.GeoDataFrame({"key": ["x"]}, geometry=[geometry], crs=4326)
    value = convert(frame)
    assert value.to_geodataframe().geometry.iloc[0].equals_exact(geometry, 0)
    assert value.features[0].geometry.type == kind


def test_both_conversion_directions_are_independent() -> None:
    frame = source_frame()
    value = convert(frame)
    original = value.model_dump()
    frame.loc[0, "key"] = "changed"
    frame.loc[0, "count"] = 99
    frame["shape"] = gpd.GeoSeries([Point(9, 9), Point(2, 3)], crs=frame.crs)
    result = value.to_geodataframe()
    result.loc[0, "id"] = "changed"
    result.loc[0, "count"] = 98
    result["geometry"] = gpd.GeoSeries([Point(8, 8), Point(2, 3)], crs=result.crs)
    assert value.model_dump() == original
    assert value.to_geodataframe().loc[0, "count"] == 2**60 + 1


def test_empty_conversion() -> None:
    value = convert(source_frame().iloc[:0])
    assert value.ids == []
    result = value.to_geodataframe(id_column="key", geometry_column="shape")
    assert result.empty
    assert list(result.columns) == ["key", "shape"]
    assert result.crs == source_frame().crs
    assert convert(result) == value


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
        convert(frame)


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
    assert convert(frame).features[0].properties["extra"] == expected


def test_invalid_frame_contracts() -> None:
    frame = source_frame()
    with pytest.raises(ValueError, match="unique"):
        convert(pd.concat([frame, frame[["key"]]], axis=1))
    with pytest.raises(ValueError, match="Missing ID"):
        convert(frame.drop(columns="key"))
    with pytest.raises(ValueError, match="distinct"):
        Locations.from_geodataframe(
            frame, collection="ex:a", unit_type="b", id_column="shape"
        )
    without_crs = gpd.GeoDataFrame({"key": ["x"]}, geometry=[Point(0, 0)])
    with pytest.raises(ValueError, match="CRS"):
        convert(without_crs)
    with pytest.raises(ValueError, match="active geometry"):
        convert(gpd.GeoDataFrame({"key": []}))
    with pytest.raises(ValueError, match="strings"):
        convert(frame.rename(columns={"count": 123}))


@pytest.mark.parametrize("name", ["id", "geometry"])
def test_property_collisions(name: str) -> None:
    frame = source_frame()
    frame[name] = ["a", "b"]
    value = convert(frame)
    with pytest.raises(ValueError, match="collides"):
        value.to_geodataframe()
    assert (
        convert(value.to_geodataframe(id_column="key", geometry_column="shape"))
        == value
    )


def test_output_columns_must_be_distinct_even_when_empty() -> None:
    value = convert(source_frame().iloc[:0])
    with pytest.raises(ValueError, match="distinct"):
        value.to_geodataframe(id_column="same", geometry_column="same")


@pytest.mark.parametrize(
    "geometry", [None, GeometryCollection(), Point(float("inf"), 0), Point()]
)
def test_invalid_geometry_is_not_filtered(geometry: object) -> None:
    frame = gpd.GeoDataFrame(
        {"key": ["x"]}, geometry=gpd.GeoSeries([geometry]), crs=4326
    )
    with pytest.raises((TypeError, ValueError)):
        convert(frame)
