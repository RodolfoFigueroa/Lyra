"""Execute documented graphs with isolated acquisition boundaries."""

import importlib
import sys
from collections.abc import Iterator
from types import ModuleType
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from lyra.contracts.errors import LocationProviderContractError
from lyra.contracts.resources import IOConfig
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.context import RunContext
from lyra.sdk.plugin_cli import render_manifest
from tests.resource_helpers import census_override
from tests.smoke_plugin_helpers import SMOKE_PLUGIN_DIR


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
    assert render_manifest(SMOKE_PLUGIN_DIR)
    database = create_autospec(plugin.resources["census_db"], instance=True)
    records = gpd.GeoDataFrame(
        {"area_code": ["source-z"], "population": [10]},
        geometry=[Point(-99, 19)],
        crs="EPSG:4326",
        index=[42],
    ).to_crs("EPSG:6372")
    if empty:
        records = records.iloc[:0]
    original = records.copy()
    database.query_geodataframe.return_value = records.to_crs(4326)
    plain = pd.DataFrame(records.drop(columns="geometry"))
    database.query_dataframe.return_value = plain
    factory = Mock(return_value=create_autospec(RunContext, instance=True))
    runner = GraphExecutor(
        LocalRunner.from_definitions({"example": plugin}).catalog,
        factory,
        resource_overrides={
            "census_db": census_override(plugin.resources["census_db"], database)
        },
    )
    fixed = runner.execute(
        runner.catalog.implementations["example"].prepare_resolved_invocation(
            "example_city_population", {"parameters": {}}
        )
    ).outputs
    pd.testing.assert_frame_equal(
        fixed["example_city_population"], plain.reset_index(drop=True)
    )
    database.query_dataframe.assert_called_once()
    assert database.query_dataframe.call_args.kwargs["params"] == {"year": 2020}
    assert set(
        plugin.describe("example_city_population").manifest.request_schema["properties"]
    ) == {"parameters"}
    invocation = prepare_execution(
        runner.catalog,
        {
            "metric": "example_population_baseline",
            "parameters": {"baseline_year": 2024},
            "inputs": {"location": {"provider": "smoke_areas", "ids": ["area-1"]}},
        },
    )
    outputs = runner.execute(invocation).outputs
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
        runner.catalog.implementations["example"].prepare_resolved_invocation(
            "example_city_source", {"parameters": {}}
        )
    ).outputs
    pd.testing.assert_frame_equal(
        acquired["example_city_records"], plain.reset_index(drop=True)
    )
    database.query_dataframe.assert_called_once()
    database.reset_mock()
    direct = example.population_baseline(
        location=plugin.resources["locations"](IOConfig(), {}).resolve(["area-1"]),
        parameters=example.population_baseline.__annotations__["parameters"](
            baseline_year=2024
        ),
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


def test_smoke_provider_rejects_unknown_ids(example: ModuleType) -> None:
    runner = LocalRunner.from_definitions({"example": example.create_plugin()})
    with pytest.raises(GraphExecutionError) as caught:
        runner.run(
            "smoke_table_metric",
            inputs={
                "location": {
                    "provider": "smoke_areas",
                    "ids": ["area-1", "unknown"],
                }
            },
            parameters={"value": 7},
        )
    assert isinstance(caught.value.__cause__, LocationProviderContractError)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(
    "name", ["smoke_table_metric", "smoke_summary_metric", "smoke_progress_metric"]
)
def test_smoke_outputs_keep_integer_dtype(
    example: ModuleType, name: str, *, empty: bool
) -> None:
    plugin = example.create_plugin()
    inputs = {
        "location": {"provider": "smoke_areas", "ids": [] if empty else ["area-1"]}
    }
    if name != "smoke_summary_metric":
        inputs["parameters"] = {"value": 7}
    runner = GraphExecutor(
        LocalRunner.from_definitions({"example": plugin}).catalog,
        Mock(return_value=create_autospec(RunContext, instance=True)),
    )
    outputs = runner.execute(
        prepare_execution(
            runner.catalog,
            {
                "metric": name,
                "inputs": {"location": inputs["location"]},
                "parameters": inputs.get("parameters", {}),
            },
        )
    ).outputs
    frame = outputs[name]
    assert frame.value.dtype == "int64"
    assert isinstance(frame.index, pd.RangeIndex)
    assert frame.value.tolist() == (
        [] if empty else [1 if name == "smoke_summary_metric" else 7]
    )
