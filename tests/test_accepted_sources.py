"""Concrete public selections from registered canonical routes."""

from typing import Any, cast

import pytest

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition
from test_support.routing_plugins import provider, routed_plugins, score_plugin


def test_multiple_inputs_and_edition_specific_reachability() -> None:
    definitions = routed_plugins()
    definitions["second_metric"] = score_plugin(target="second", name="score_second")
    definitions["second_provider"] = PluginDefinition(
        resources={"second": provider("second")}
    )
    catalog = compile_live_catalog(definitions)
    places = catalog.accepted_sources["score"]["places"]
    assert {key: binding.operations for key, binding in places.items()} == {
        "native": (),
        "source": ("select_0",),
        "other": ("select_1",),
    }
    assert set(catalog.accepted_sources["score"]["reference"]) == {"other"}
    assert set(catalog.accepted_sources["score_second"]["places"]) == {"second"}
    with pytest.raises(TypeError):
        cast("Any", places)["invalid"] = places["native"]


def test_native_only_and_private() -> None:
    catalog = compile_live_catalog(
        {
            "metrics": score_plugin(private=True),
            "providers": PluginDefinition(
                resources={key: provider(key) for key in ("native", "other")}
            ),
        }
    )
    assert all(
        len(sources) == 1 for sources in catalog.accepted_sources["score"].values()
    )
    with pytest.raises(ValueError, match="Unknown public metric"):
        prepare_execution(catalog, {"metric": "score"})
