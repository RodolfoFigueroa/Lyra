"""Canonical eligibility is validated without executing handlers."""

import geopandas as gpd
import pytest

from lyra.contracts.dependencies import LocationDependency
from lyra.contracts.plugins import (
    NODE_MANIFEST_ADAPTER,
    PluginInfo,
)
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.location_operations import Places, definition
from test_support.routing_plugins import conversion, provider


def test_canonical_shape_is_validated() -> None:
    declaration = definition().describe("select_places").manifest
    operation = declaration.model_dump(exclude={"inputs"})
    operation["inputs"] = declaration.inputs
    operation.update(canonical=True, location_inputs={})
    with pytest.raises(ValueError, match="exactly one"):
        NODE_MANIFEST_ADAPTER.validate_python(operation)


def test_decorator_captures_canonical_status() -> None:

    @location_operation(
        code_revision="1",
        name="canonical_places",
        description="Select places",
        canonical=True,
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def select(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return source

    plugin = PluginDefinition(
        location_operations=[select], resources={"places": Places}
    )
    declaration = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    assert declaration.nodes[0].kind == "location_operation"
    assert declaration.nodes[0].canonical
    described = plugin.describe("canonical_places").manifest
    assert described.kind == "location_operation"
    assert described.canonical
    assert len(compile_live_catalog({"test": plugin}).canonical_edges) == 1


def test_conversion_cycle_is_not_an_execution_cycle() -> None:
    catalog = compile_live_catalog(
        {
            "cycle": PluginDefinition(
                resources={key: provider(key) for key in "ab"},
                location_operations=[
                    conversion("forward", "a", "b"),
                    conversion("reverse", "b", "a"),
                ],
            )
        }
    )
    a, b = "a", "b"
    assert catalog.routes[a, b] == ("forward",)
    assert catalog.routes[b, a] == ("reverse",)
    assert catalog.routes[a, a] == ()
