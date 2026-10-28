from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from lyra.sdk import (
    MetricResultError,
    PluginDefinition,
    TableColumn,
    TableOutput,
    dataset,
    metric,
)
from lyra.sdk.results import normalize_native_result, serialize_outputs
from shapely.geometry import Point

OUTPUT = TableOutput(
    columns=[
        TableColumn(
            name="declared", type="integer", unit=None, description="Descriptive"
        )
    ]
)


@pytest.mark.parametrize("role", ["metric", "dataset"])
def test_native_outputs_preserve_identity_without_serializing(role: str) -> None:
    decorator = metric if role == "metric" else dataset

    @decorator(name="producer", description="Producer", outputs={"one": OUTPUT})
    def handler() -> object:
        return None

    frame = pd.DataFrame(
        {"producer_id": ["b", "a", "c"], "value": [None, "text", 2]}, index=[7, 7, 2]
    )
    plugin = PluginDefinition(nodes=[handler])
    with patch("lyra.sdk.results.serialize_outputs", side_effect=AssertionError):
        outputs = plugin.normalize_result("producer", frame)
    assert outputs["one"] is frame
    assert outputs["one"].index.tolist() == [7, 7, 2]
    assert serialize_outputs("producer", outputs) == {
        "one": [
            {"producer_id": "b", "value": None},
            {"producer_id": "a", "value": "text"},
            {"producer_id": "c", "value": 2},
        ]
    }


@pytest.mark.parametrize(
    "value",
    [None, {"one": pd.DataFrame()}, pd.Series([1]), Path("raster.tif"), object()],
)
def test_invalid_single_output(value: object) -> None:
    with pytest.raises(MetricResultError, match=r"one.*DataFrame"):
        normalize_native_result("producer", {"one": OUTPUT}, value)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"one": pd.DataFrame()},
        {"one": pd.DataFrame(), "two": None},
        {"one": pd.DataFrame(), "two": pd.DataFrame(), "extra": pd.DataFrame()},
        pd.DataFrame(),
    ],
)
def test_invalid_multi_output(value: object) -> None:
    with pytest.raises(MetricResultError):
        normalize_native_result("producer", {"one": OUTPUT, "two": OUTPUT}, value)


@pytest.mark.parametrize("empty", [False, True])
def test_mixed_tables_and_projected_geometry(*, empty: bool) -> None:
    ordinary = pd.DataFrame({"id": ["owned"]}, index=["private"])
    spatial = gpd.GeoDataFrame(
        {"id": ["parcel"], "shape": [Point(111319.49079327357, 0)]},
        geometry="shape",
        crs=3857,
        index=["hidden"],
    )
    if empty:
        ordinary = ordinary.iloc[:0]
        spatial = spatial.iloc[:0]
    before = spatial.copy()
    outputs = normalize_native_result(
        "producer", {"one": OUTPUT, "two": OUTPUT}, {"one": ordinary, "two": spatial}
    )
    assert outputs["one"] is ordinary
    assert outputs["two"] is spatial
    encoded = serialize_outputs("producer", outputs)
    assert encoded["one"] == ([] if empty else [{"id": "owned"}])
    geo = encoded["two"]
    assert isinstance(geo, dict)
    assert geo["type"] == "FeatureCollection"
    if empty:
        assert geo == {"type": "FeatureCollection", "features": []}
    else:
        assert geo["features"] == [
            {
                "type": "Feature",
                "properties": {"id": "parcel"},
                "geometry": {"type": "Point", "coordinates": [pytest.approx(1), 0]},
            }
        ]
    pd.testing.assert_frame_equal(spatial, before)
    assert spatial.crs == before.crs
    assert json.loads(json.dumps(encoded, allow_nan=False)) == encoded


@pytest.mark.parametrize("spatial", [False, True])
def test_scalars_and_nulls(*, spatial: bool) -> None:
    frame = pd.DataFrame(
        {
            "integer": [np.int64(3)],
            "float": [np.float64(1.5)],
            "bool": [True],
            "date": [date(2024, 1, 2)],
            "time": [pd.Timestamp(datetime(2024, 1, 2, tzinfo=UTC))],
            "none": [None],
            "nan": [np.nan],
            "na": [pd.NA],
            "nat": [pd.NaT],
        }
    )
    if spatial:
        frame = gpd.GeoDataFrame(frame, geometry=gpd.GeoSeries([None]), crs=4326)
    result = serialize_outputs("producer", {"one": frame})["one"]
    if spatial:
        assert isinstance(result, dict)
        features = result["features"]
        assert isinstance(features, list)
        feature = features[0]
        assert isinstance(feature, dict)
        row = feature["properties"]
    else:
        assert isinstance(result, list)
        row = result[0]
    assert isinstance(row, dict)
    assert row["integer"] == 3
    assert row["float"] == pytest.approx(1.5)
    assert row["bool"] is True
    assert isinstance(row["date"], str)
    assert row["date"].startswith("2024-01-02")
    assert isinstance(row["time"], str)
    assert row["time"].startswith("2024-01-02T00:00:00")
    assert all(row[key] is None for key in ["none", "nan", "na", "nat"])


@pytest.mark.parametrize("empty", [False, True])
def test_unknown_crs_fails_even_when_empty(*, empty: bool) -> None:
    frame = gpd.GeoDataFrame(geometry=[] if empty else [Point(0, 0)])
    assert normalize_native_result("producer", {"one": OUTPUT}, frame)["one"] is frame
    with pytest.raises(MetricResultError, match=r"one.*CRS"):
        serialize_outputs("producer", {"one": frame})


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("value", [object(), Path("artifact.tif")])
def test_unsupported_cells_fail_without_stringification(
    *, spatial: bool, value: object
) -> None:
    frame = pd.DataFrame({"value": [value]})
    if spatial:
        frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0)], crs=4326)
    with pytest.raises(MetricResultError, match=r"one.*unsupported"):
        serialize_outputs("producer", {"one": frame})


@pytest.mark.parametrize("empty", [False, True])
def test_missing_active_geometry_has_serialization_context(*, empty: bool) -> None:
    frame = gpd.GeoDataFrame({"value": [] if empty else [1]})
    outputs = normalize_native_result("producer", {"one": OUTPUT}, frame)
    assert outputs["one"] is frame
    with pytest.raises(MetricResultError, match="active geometry") as error:
        serialize_outputs("producer", outputs)
    assert error.value.metric == "producer"
    assert error.value.path == "output 'one' serialization"
    assert isinstance(error.value.__cause__, AttributeError)


@pytest.mark.parametrize("spatial", [False, True])
def test_single_empty_table(*, spatial: bool) -> None:
    frame = (
        gpd.GeoDataFrame({"value": []}, geometry=[], crs=4326)
        if spatial
        else pd.DataFrame({"value": []})
    )
    outputs = normalize_native_result("producer", {"one": OUTPUT}, frame)
    assert outputs["one"] is frame
    assert serialize_outputs("producer", outputs) == {
        "one": {"type": "FeatureCollection", "features": []} if spatial else []
    }
