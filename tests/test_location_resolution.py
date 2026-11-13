"""Managed provider acquisition preserves scope, validation, and ownership."""

from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import ValidationError
from typing_extensions import override

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.resources import IOConfig, ResourceConfig
from lyra.runtime.locations import LocationResolver, resolve_locations
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from test_support.location_resolution import (
    PROVIDERS,
    RESOURCES,
    Provider,
    Roads,
    frame_features,
    resolve,
    selection,
)


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))
    return events


def test_multiple_inputs_share_lifecycle_and_preserve_scope_and_order(
    lifecycle: list[str],
) -> None:
    values = resolve({"first": selection("b", "a", "b"), "second": selection("a")})
    assert values["first"].ids == ["b", "a"]
    assert values["second"].ids == ["a"]
    assert values["first"].contract is not None
    assert values["first"].contract.provider_scope == PROVIDERS[Provider.id].scope
    assert values["first"].to_geodataframe().original.tolist() == ["b", "a"]
    assert lifecycle == ["setup", "close"]


def test_equal_bare_ids_from_different_providers_are_distinct() -> None:
    values = resolve(
        {"units": selection("a"), "roads": selection("a", provider=Roads.id)}
    )
    assert values["units"].ids == values["roads"].ids == ["a"]
    assert values["units"].contract != values["roads"].contract
    assert values["roads"].crs.properties.name == "EPSG:27700"


@pytest.mark.parametrize(
    "invalid",
    [
        {"provider": "unknown", "ids": ["a"]},
        {"provider": Provider.id, "ids": [""]},
        {"provider": Provider.id, "ids": [1]},
        {"provider": 1, "ids": ["a"]},
    ],
)
def test_all_inputs_validated_before_setup(lifecycle: list[str], invalid: dict) -> None:
    malformed = LocationSelection.model_construct(**invalid)
    with pytest.raises((ValueError, TypeError, KeyError, LocationSelectionError)):
        resolve({"first": selection("a"), "second": malformed})
    assert lifecycle == []


def test_no_inputs_and_empty_selection_need_no_resources(lifecycle: list[str]) -> None:
    assert resolve({}) == {}
    value = resolve({"empty": selection()})["empty"]
    assert value.ids == []
    assert value.contract is not None
    assert value.contract.provider_scope == PROVIDERS[Provider.id].scope
    assert value.to_geodataframe().columns.tolist() == [
        "location_id",
        "geometry",
        "original",
    ]
    assert str(value.to_geodataframe().crs) == "EPSG:4326"
    assert lifecycle == []


@pytest.mark.parametrize("returned", [[], ["a"], ["a", "b", "extra"], ["a", "a", "b"]])
def test_exact_membership_is_required(
    returned: list[str], monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    monkeypatch.setattr(
        Provider, "resolve", lambda _self, _ids: frame_features(returned)
    )
    with pytest.raises(LocationProviderContractError):
        resolve({"places": selection("a", "b")})
    assert lifecycle == ["setup", "close"]


@pytest.mark.parametrize("value", [None, {}, pd.DataFrame(), gpd.GeoDataFrame()])
def test_invalid_output_is_a_contract_violation(
    value: object, monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    monkeypatch.setattr(Provider, "resolve", lambda _self, _ids: value)
    with pytest.raises(LocationProviderContractError):
        resolve({"places": selection("a")})
    assert lifecycle == ["setup", "close"]


@pytest.mark.parametrize("method", ["setup", "resolve", "close"])
def test_lifecycle_failures_do_not_return_results(
    method: str, monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    failure = RuntimeError("private credentials must stay in chained cause")
    monkeypatch.setattr(Provider, method, Mock(side_effect=failure))
    with pytest.raises((LocationAcquisitionError, RuntimeError)) as caught:
        resolve({"places": selection("a")})
    if method != "close":
        assert lifecycle[-1] == "close"
    assert caught.value is failure or caught.value.__cause__ is failure


def test_cleanup_does_not_mask_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition = RuntimeError("acquisition")
    cleanup = RuntimeError("cleanup")
    monkeypatch.setattr(Provider, "resolve", Mock(side_effect=acquisition))
    monkeypatch.setattr(Provider, "close", Mock(side_effect=cleanup))
    with pytest.raises(LocationAcquisitionError) as caught:
        resolve({"places": selection("a")})
    assert caught.value.__cause__ is acquisition


def test_borrowed_scope_reuses_provider_and_owner_closes_after_failure(
    lifecycle: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = ResourceScope(RESOURCES)
    resolver = LocationResolver(scope, providers=PROVIDERS)
    with scope.activate():
        first = resolver.resolve({"places": selection("a")})["places"]
        assert first.ids == ["a"]
        assert lifecycle == ["setup"]
        monkeypatch.setattr(
            Provider, "resolve", Mock(side_effect=OSError("unavailable"))
        )
        with pytest.raises(LocationAcquisitionError):
            resolver.resolve({"places": selection("b")})
        assert lifecycle == ["setup"]
    assert lifecycle == ["setup", "close"]


def test_frame_metadata_cannot_replace_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    records = frame_features(["a"])
    records.attrs.update(provider="forged", provider_scope={"provider_id": "forged"})
    monkeypatch.setattr(Provider, "resolve", lambda _self, _ids: records)
    value = resolve({"places": selection("a")})["places"]
    assert value.contract is not None
    assert value.contract.provider_scope == PROVIDERS[Provider.id].scope


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "null_id",
        "numeric_id",
        "duplicate_column",
        "crs",
        "null_geometry",
    ],
)
def test_provider_schema_rejects_invalid_native_records(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = frame_features(["a"])
    if mutation == "extra":
        records["extra"] = 1
    elif mutation == "missing":
        records = records.drop(columns="original")
    elif mutation == "null_id":
        records["location_id"] = None
    elif mutation == "numeric_id":
        records["location_id"] = 1
    elif mutation == "duplicate_column":
        records = pd.concat([records, records[["original"]]], axis=1)
    elif mutation == "crs":
        records = records.to_crs(27700)
    else:
        records["geometry"] = None
    monkeypatch.setattr(Provider, "resolve", lambda _self, _ids: records)
    with pytest.raises(LocationProviderContractError):
        resolve({"places": selection("a")})


def test_configuration_and_override_use_registered_schema() -> None:
    class Settings(IOConfig):
        label: str

    class Configured(LocationProvider[Settings]):
        id = Provider.id
        schema = Provider.schema
        config_model = Settings
        calls: ClassVar[list[str]] = []

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            self.calls.append(self.config.label)
            return frame_features(ids)

    class Override(Configured):
        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            result = super().resolve(ids)
            result["extra"] = 1
            return result

    resources = {"locations": Configured}
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources=resources)}
    )
    configuration = {"locations": ResourceConfig(config={"label": "configured"})}
    values = resolve_locations(
        {"places": selection("a")},
        providers=registry,
        resources=resources,
        configuration=configuration,
    )
    assert values["places"].ids == ["a"]
    assert Configured.calls == ["configured"]
    with pytest.raises(LocationProviderContractError):
        resolve_locations(
            {"places": selection("a")},
            providers=registry,
            resources=resources,
            configuration=configuration,
            overrides={"locations": Override},
        )


@pytest.mark.parametrize("ids", [[1], [None], [""], "a", ("a",)])
def test_request_ids_are_strict(ids: object) -> None:
    with pytest.raises(ValidationError):
        LocationSelection.model_validate({"provider": Provider.id, "ids": ids})
