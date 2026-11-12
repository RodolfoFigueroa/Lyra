from __future__ import annotations

import asyncio
import sys
from importlib import metadata
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pandera.pandas as pa
import pytest

from lyra.contracts.units import Unit
from lyra.data.locations import (
    Locations,  # ruff: ignore[typing-only-first-party-import]
)
from lyra.runtime.resources import ResourceScope
from lyra.sdk.loading import (
    PluginLoadError,
    load_installed_plugin_definition,
    load_plugin_definition,
)
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_helpers import node_contract, output_schema


def _output() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "value": pa.Column(
                int, description="Example value.", metadata={"unit": Unit.COUNT}
            )
        }
    )


@metric(
    code_revision="1",
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


def _installed_entries(
    monkeypatch: pytest.MonkeyPatch, entries: list[metadata.EntryPoint]
) -> Mock:
    selected = SimpleNamespace(entry_points=entries)
    lookup = Mock(return_value=selected)
    monkeypatch.setattr(metadata, "distribution", lookup)
    return lookup


def _entry(
    value: str = "fixture_plugin:create_plugin",
    *,
    name: str = "plugin",
    group: str = "lyra.plugins",
) -> metadata.EntryPoint:
    return metadata.EntryPoint(name=name, value=value, group=group)


@pytest.mark.parametrize("name", ["my-plugin", "My_Plugin", "MY...Plugin"])
def test_installed_loader_selects_normalized_distribution(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _install_factory(monkeypatch, _definition)
    lookup = _installed_entries(monkeypatch, [_entry()])

    definition = load_installed_plugin_definition(name)

    assert definition.metric_names == ("example",)
    lookup.assert_called_once_with("my-plugin")


@pytest.mark.parametrize(
    "entries",
    [[], [_entry(name="other")], [_entry(group="other")], [_entry(), _entry()]],
)
def test_installed_loader_requires_unambiguous_registration(
    monkeypatch: pytest.MonkeyPatch, entries: list[metadata.EntryPoint]
) -> None:
    factory = Mock(side_effect=AssertionError("must not invoke"))
    _install_factory(monkeypatch, factory)
    _installed_entries(monkeypatch, entries)
    with pytest.raises(PluginLoadError, match=r"my-plugin.*exactly one.*lyra.plugins"):
        load_installed_plugin_definition("my-plugin")
    factory.assert_not_called()


def test_installed_loader_missing_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = _installed_entries(monkeypatch, [])
    lookup.side_effect = metadata.PackageNotFoundError("absent")
    with pytest.raises(PluginLoadError, match=r"absent.*not installed"):
        load_installed_plugin_definition("absent")


def test_installed_loader_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = _installed_entries(monkeypatch, [])
    with pytest.raises(PluginLoadError, match="Invalid plugin distribution"):
        load_installed_plugin_definition("bad/name")
    lookup.assert_not_called()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (42, "must resolve to a callable"),
        (lambda value: value, "must declare no parameters"),
        (lambda value=None: value, "must declare no parameters"),
        (lambda *args: args, "must declare no parameters"),
        (lambda **kwargs: kwargs, "must declare no parameters"),
        (object, "must return PluginDefinition"),
    ],
)
def test_installed_loader_validates_factory(
    monkeypatch: pytest.MonkeyPatch, factory: object, message: str
) -> None:
    _install_factory(monkeypatch, factory)
    _installed_entries(monkeypatch, [_entry()])
    with pytest.raises(PluginLoadError, match=message) as error:
        load_installed_plugin_definition("my-plugin")
    assert "my-plugin" in str(error.value)
    assert "fixture_plugin:create_plugin" in str(error.value)


def test_installed_loader_rejects_async_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def asynchronous() -> PluginDefinition:
        await asyncio.sleep(0)
        return _definition()

    class AsyncFactory:
        async def __call__(self) -> PluginDefinition:
            return _definition()

    _installed_entries(monkeypatch, [_entry()])

    def wrapped() -> object:
        return asynchronous()

    for factory in (asynchronous, AsyncFactory(), wrapped):
        _install_factory(monkeypatch, factory)
        with pytest.raises(PluginLoadError, match=r"my-plugin.*must be synchronous"):
            load_installed_plugin_definition("my-plugin")


@pytest.mark.parametrize(
    "failure", [ImportError("missing dependency"), RuntimeError("broken import")]
)
def test_installed_loader_wraps_import_errors(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _installed_entries(monkeypatch, [_entry()])
    monkeypatch.setattr(metadata.EntryPoint, "load", Mock(side_effect=failure))
    with pytest.raises(PluginLoadError, match=r"Could not import.*my-plugin") as error:
        load_installed_plugin_definition("my-plugin")
    assert error.value.__cause__ is failure
    assert str(failure) in str(error.value)


def test_installed_loader_wraps_factory_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = ValueError("factory exploded")

    def factory() -> PluginDefinition:
        raise failure

    _install_factory(monkeypatch, factory)
    _installed_entries(monkeypatch, [_entry()])
    with pytest.raises(PluginLoadError, match=r"my-plugin.*factory exploded") as error:
        load_installed_plugin_definition("my-plugin")
    assert error.value.__cause__ is failure


def test_installed_loading_does_not_execute_or_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition = Mock(side_effect=AssertionError("must not acquire"))
    monkeypatch.setattr(ResourceScope, "get", acquisition)
    contract = node_contract()

    @location_operation(
        code_revision="1",
        name="inert",
        description="Inert operation.",
        locations={"source": contract},
        output_contract=contract,
        output=output_schema(),
    )
    def operation(source: Locations) -> object:
        raise AssertionError(source)

    def factory() -> PluginDefinition:
        return PluginDefinition(metrics=[_handler], location_operations=[operation])

    _install_factory(monkeypatch, factory)
    selected = SimpleNamespace(
        entry_points=[_entry(), _entry("unrelated:factory", name="other")]
    )
    unrelated = SimpleNamespace(entry_points=[_entry("disabled:factory")])
    distributions = {"selected": selected, "disabled": unrelated}
    monkeypatch.setattr(metadata, "distribution", distributions.__getitem__)

    definition = load_installed_plugin_definition("selected")

    assert definition.metric_names == ("example",)
    assert definition.location_operation_names == ("inert",)
    acquisition.assert_not_called()
