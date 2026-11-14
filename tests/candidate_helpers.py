"""Shared isolated provider/storage test boundaries."""

from unittest.mock import Mock, create_autospec

import geopandas as gpd
import ibis.expr.types as ir
import pytest

from lyra.contracts.resources import ResourceConfig
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning.provider_registry import RegisteredProvider
from lyra.runtime.resources import ResourceScope
from lyra.sdk.provider_schema import ProviderSchema
from tests.selector_helpers import ManagedProvider


def resources(
    monkeypatch: pytest.MonkeyPatch,
    target: RegisteredProvider,
    output: gpd.GeoDataFrame,
    events: list[str],
    *,
    failure: str | None = None,
) -> tuple[ResourceScope, LocationResolver, Mock]:
    def setup(_self: ManagedProvider) -> None:
        events.append("setup")
        if failure == "setup":
            message = "setup failed"
            raise RuntimeError(message)

    def select(
        _self: ManagedProvider, expression: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        events.append("query")
        assert isinstance(expression, ir.Table)
        assert schema is target.schema
        if failure in {"query", "decode"}:
            message = f"{failure} failed"
            raise RuntimeError(message)
        return output

    monkeypatch.setattr(ManagedProvider, "secret_names", ())
    monkeypatch.setattr(ManagedProvider, "setup", setup)
    monkeypatch.setattr(ManagedProvider, "close", lambda _self: events.append("close"))
    monkeypatch.setattr(ManagedProvider, "select", select)
    exact = Mock(side_effect=AssertionError("Unexpected exact-ID acquisition"))
    monkeypatch.setattr(
        ManagedProvider,
        "resolve",
        create_autospec(ManagedProvider.resolve, side_effect=exact),
    )
    scope = ResourceScope(
        {"managed": ManagedProvider},
        {"managed": ResourceConfig(config={"label": "fake"})},
    )
    return scope, LocationResolver(scope, providers={"managed": target}), exact
