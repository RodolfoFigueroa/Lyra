"""Exercise the managed authoring example without external acquisition."""

import importlib
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import Point

from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.operations import execute_operation
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.runtime.resources import ResourceScope


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(
        str(Path(__file__).parents[1] / "examples/location-plugin")
    )
    return importlib.import_module("location_plugin.managed")


def blocks() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"location_id": ["a", "b", "c"], "tree_id": ["t", "t", "far"]},
        geometry=[
            Point(500000, 2200000),
            Point(500010, 2200000),
            Point(501000, 2200000),
        ],
        crs=32614,
    ).to_crs(4326)


def trees() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"location_id": ["t"], "height": [10.0]},
        geometry=[Point(500000, 2200000)],
        crs=32614,
    )


@pytest.mark.parametrize("empty", [False, True])
def test_example_callbacks_and_declarations(
    example: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, empty: bool
) -> None:
    compile_live_catalog({"managed": example.plugin})
    binding = BoundPlugin(example.plugin)
    source_provider = capture_provider("block_source", example.CityBlocks)
    target = capture_provider("tree_source", example.Trees)
    source = blocks()
    source_provider.schema.validate(source)
    # Repeated represented blocks must not duplicate a target in a source group.
    repeated = source.iloc[[1, 0, 0, 2]].copy()
    invocation = binding.prepare_resolved_invocation(
        "nearby_trees",
        {
            "blocks": NativeLocationInput(repeated, source_provider.contract.reference),
            "parameters": {},
        },
    )
    expression = binding.invoke_target_selector(invocation, target)
    assert expression is not None
    for dialect in ("duckdb", "postgres"):
        sql = str(ibis.to_sql(expression, dialect=dialect))
        assert "ST_DWITHIN" in sql.upper()
        assert "height" in sql
    candidates = trees().iloc[:0] if empty else trees()
    target.schema.validate(candidates)
    path = tmp_path / "trees.parquet"
    candidates.to_parquet(path)
    monkeypatch.setattr(example.Trees, "setup", Mock())
    monkeypatch.setattr(example.Trees, "close", Mock())
    selection = Mock(return_value=candidates)
    monkeypatch.setattr(example.Trees, "select", selection)
    exact = Mock(side_effect=AssertionError("Candidates must be reused"))
    monkeypatch.setattr(example.Trees, "resolve", exact)
    scope = ResourceScope(
        {"tree_source": example.Trees},
        {"tree_source": example.ResourceConfig(config={"path": str(path)})},
    )
    resolver = LocationResolver(scope, providers={example.Trees.id: target})
    with scope.activate():
        result = execute_operation(binding, invocation, resolver)
    selection.assert_called_once()
    exact.assert_not_called()
    assert result.targets.frame.crs is not None
    assert result.targets.frame.crs.to_epsg() == 32614
    assert result.groups == {
        "a": () if empty else ("t",),
        "b": () if empty else ("t",),
        "c": (),
    }
    assert_geodataframe_equal(repeated, source.iloc[[1, 0, 0, 2]])
    assert example.assigned_trees(source) == {"a": ("t",), "b": ("t",), "c": ("far",)}


def test_simple_selectors(example: ModuleType) -> None:
    relation = ibis.table(
        {
            "location_id": "string",
            "height": "float64",
            "geometry": dt.GeoSpatial(srid=32614),
        },
        name="trees",
    )
    assert example.select_all_trees(blocks(), relation).equals(relation)
    assert example.select_tall_trees(blocks(), relation).equals(
        relation.filter(relation.height > 5)
    )
    empty = example.select_nearby_trees(
        blocks().iloc[:0], relation, parameters=example.TreeParameters()
    )
    assert empty.equals(relation.filter([False]))


def test_example_resource_configuration(
    example: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Configuration validates a local file without connecting to DuckDB.
    path = tmp_path / "trees.parquet"
    trees().to_parquet(path)
    config = example.Trees.config_model(path=str(path))
    assert config.path == str(path)
    database = example.PostgresIO.config_model.model_validate(
        example.resource_config["city_db"].config
    )
    assert database.database == "city"
    acquisition = Mock(side_effect=AssertionError("Declaration must not acquire"))
    monkeypatch.setattr(example.PostgresIO, "setup", acquisition)
    monkeypatch.setattr(example.Trees, "setup", acquisition)
    compile_live_catalog({"managed": example.plugin})
    acquisition.assert_not_called()
