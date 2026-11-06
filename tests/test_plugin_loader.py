from __future__ import annotations

import sys
from types import ModuleType

import pandera.pandas as pa
import pytest

from lyra.contracts.units import Unit
from lyra.data.locations import (
    Locations,  # ruff: ignore[typing-only-first-party-import]
)
from lyra.sdk.loading import PluginLoadError, load_plugin_definition
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import node_contract


def _output() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "value": pa.Column(
                int, description="Example value.", metadata={"unit": Unit.COUNT}
            )
        }
    )


@metric(
    locations={"location": node_contract()},
    name="example",
    description="Example.",
    outputs={"example": _output()},
)
def _handler(location: Locations) -> object:
    raise AssertionError(location)


def _definition() -> PluginDefinition:
    return PluginDefinition(metrics=[_handler])


def _install_factory(
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
) -> None:
    module = ModuleType("fixture_plugin")
    monkeypatch.setattr(module, "create_plugin", factory, raising=False)
    monkeypatch.setitem(sys.modules, "fixture_plugin", module)


def test_loader_returns_fresh_factory_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_factory(monkeypatch, _definition)

    first = load_plugin_definition("fixture_plugin:create_plugin")
    second = load_plugin_definition("fixture_plugin:create_plugin")

    assert first.metric_names == ("example",)
    assert second.metric_names == ("example",)
    assert first is not second


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (42, "must resolve to a callable"),
        (lambda value: value, "must declare no parameters"),
        (object, "must return PluginDefinition"),
    ],
)
def test_loader_rejects_invalid_factories(
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
    match: str,
) -> None:
    _install_factory(monkeypatch, factory)

    with pytest.raises(PluginLoadError, match=match):
        load_plugin_definition("fixture_plugin:create_plugin")


def test_loader_rejects_async_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_plugin() -> PluginDefinition:  # ruff: ignore[unused-async]
        return _definition()

    _install_factory(monkeypatch, create_plugin)

    with pytest.raises(PluginLoadError, match="must be synchronous"):
        load_plugin_definition("fixture_plugin:create_plugin")


def test_loader_wraps_factory_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def create_plugin() -> PluginDefinition:
        msg = "factory exploded"
        raise ValueError(msg)

    _install_factory(monkeypatch, create_plugin)

    with pytest.raises(PluginLoadError, match="factory exploded"):
        load_plugin_definition("fixture_plugin:create_plugin")


@pytest.mark.parametrize(
    "factory_ref",
    ["example.plugin", "example.plugin:create:again", "bad-name:create_plugin"],
)
def test_loader_rejects_malformed_references(factory_ref: str) -> None:
    with pytest.raises(PluginLoadError, match="module:attribute"):
        load_plugin_definition(factory_ref)
