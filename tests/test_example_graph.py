"""Execute documented graphs with isolated acquisition boundaries."""

import importlib
import sys
from collections.abc import Iterator
from types import ModuleType
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk import RunContext
from lyra.sdk.db import DatabaseIO
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.graph import build_graph
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.plugin_cli import check_manifest
from lyra.sdk.results import serialize_outputs
from shapely.geometry import Point

from tests.resource_helpers import database_override
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR, feature_collection


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    for name in list(sys.modules):
        if name == "smoke_plugin" or name.startswith("smoke_plugin."):
            monkeypatch.delitem(sys.modules, name)
    yield importlib.import_module("smoke_plugin.plugin")
    for name in list(sys.modules):
        if name == "smoke_plugin" or name.startswith("smoke_plugin."):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("empty", [False, True])
def test_documented_graphs(example: ModuleType, *, empty: bool) -> None:
    plugin = example.create_plugin()
    manifest = plugin.manifest(
        plugin=PluginInfo(name="example", version="1"), factory="fake:create"
    )
    assert check_manifest(SMOKE_PLUGIN_DIR) == (True, "")
    database = create_autospec(DatabaseIO, instance=True)
    records = gpd.GeoDataFrame(
        {"area_code": ["source-z"], "population": [10]},
        geometry=[Point(-99, 19)],
        crs="EPSG:4326",
        index=[42],
    ).to_crs("EPSG:6372")
    if empty:
        records = records.iloc[:0]
    original = records.copy()
    database.query_geodataframe.return_value = records
    plain = pd.DataFrame(records.drop(columns="geometry"))
    database.query_dataframe.return_value = plain
    factory = Mock(return_value=create_autospec(RunContext, instance=True))
    runner = GraphExecutor(
        build_graph({"example": manifest.nodes}),
        {"example": plugin},
        factory,
        resource_overrides={"database": database_override(database)},
    )
    fixed = runner.execute(
        plugin.prepare_invocation("example_city_population", {"parameters": {}})
    )
    pd.testing.assert_frame_equal(fixed["example_city_population"], plain)
    database.query_dataframe.assert_called_once()
    assert database.query_dataframe.call_args.kwargs["params"] == {"year": 2020}
    assert set(
        plugin.describe("example_city_population").request_schema["properties"]
    ) == {"parameters"}
    invocation = plugin.prepare_invocation(
        "example_population_baseline",
        {
            "parameters": {"baseline_year": 2024},
            "location": feature_collection(),
        },
    )
    outputs = runner.execute(invocation)
    assert set(outputs) == {"example_population_areas", "example_population_total"}
    assert outputs["example_population_areas"] is not records
    pd.testing.assert_frame_equal(records, original)
    kwargs = database.query_geodataframe.call_args.kwargs
    assert kwargs["params"]["year"] == 2024
    assert kwargs["params"]["source_crs"] == "EPSG:4326"
    assert kwargs["crs"] == "EPSG:4326"
    assert factory.call_args_list[-1].args == ("example_selected_source",)
    encoded = serialize_outputs("example_population_baseline", outputs)
    assert encoded["example_population_total"] == [{"population": 0 if empty else 10}]
    assert_spatial_output(encoded["example_population_areas"], empty=empty)
    assert records.crs == "EPSG:6372"
    database.reset_mock()
    acquired = runner.execute(
        plugin.prepare_invocation("example_city_source", {"parameters": {}})
    )
    pd.testing.assert_frame_equal(acquired["example_city_records"], plain)
    database.query_dataframe.assert_called_once()
    database.reset_mock()
    direct = example.population_baseline(
        location=invocation.spatial["location"],
        parameters=invocation.parameters,
        records=records,
    )
    pd.testing.assert_frame_equal(direct["example_population_areas"], records)
    database.query_geodataframe.assert_not_called()


def assert_spatial_output(spatial: object, *, empty: bool) -> None:
    assert isinstance(spatial, dict)
    features = spatial["features"]
    assert isinstance(features, list)
    assert len(features) == (0 if empty else 1)
    if not empty:
        assert "id" not in features[0]
        assert features[0]["properties"]["area_code"] == "source-z"
        assert features[0]["geometry"]["coordinates"] == pytest.approx([-99, 19])
