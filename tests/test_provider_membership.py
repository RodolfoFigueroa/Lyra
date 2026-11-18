from collections.abc import Callable
from typing import cast
from unittest.mock import Mock

import geopandas as gpd
import numpy as np
import pytest
from typing_extensions import override

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    ResourceConfigurationError,
)
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import (
    RegisteredProvider,
    compile_provider_registry,
)
from lyra.runtime.provider_acquisition import (
    acquire_provider_input,
    acquire_provider_membership,
)
from lyra.runtime.resources import ResourceScope
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from tests.provider_schema_helpers import declaration, frame


class OrdinaryPlaces(LocationProvider):
    id = "places"
    schema = declaration()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        data = frame()
        return data[data.location_id.isin(ids)]


def setup(
    lookup: Callable[[list[str]], object] | None = None,
) -> tuple[RegisteredProvider, ResourceScope]:
    provider = OrdinaryPlaces
    if lookup is not None:

        def existing_ids(_self: object, ids: list[str]) -> object:
            return lookup(ids)

        provider = type("MembershipPlaces", (provider,), {"existing_ids": existing_ids})
    resources = {"places": provider}
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources=resources)}
    )
    return registry["places"], ResourceScope(resources, {})


@pytest.mark.parametrize("supported", [False, True])
def test_inherited_capability_inspection_is_declaration_only(
    monkeypatch: pytest.MonkeyPatch, *, supported: bool
) -> None:
    forbidden = Mock(side_effect=AssertionError("Acquisition during inspection"))

    def existing_ids(_self: object, ids: list[str]) -> set[str]:
        return forbidden(ids)

    parent = type(
        "Parent",
        (OrdinaryPlaces,),
        {"existing_ids": existing_ids} if supported else {},
    )
    child = type("Child", (parent,), {})
    for method in ("__init__", "setup"):
        monkeypatch.setattr(child, method, forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources={"places": child})}
    )
    assert registry["places"].membership_capable is supported
    assert registry["places"].declaration.membership_capable is supported
    forbidden.assert_not_called()


def test_unsupported_provider_retains_ordinary_execution() -> None:
    registered, resources = setup()
    with pytest.raises(LocationProviderContractError, match="does not support"):
        acquire_provider_membership(registered, ["a"], resources)
    assert not resources.instances
    with resources.activate():
        selection = PreparedProviderInput("selected", frozenset({"a"}), registered)
        assert acquire_provider_input(
            selection, resources
        ).frame.location_id.tolist() == ["a"]


@pytest.mark.parametrize("ids", [["a"], ["missing"], ["b", "missing", "a"]])
def test_membership_is_independent_of_selection_and_does_not_resolve(
    ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = Mock(side_effect=lambda requested: set(requested) & {"a", "b"})
    registered, resources = setup(lookup)
    with resources.activate():
        # An earlier exact selection must not restrict membership to that subset.
        selected = PreparedProviderInput("selected", frozenset({"a"}), registered)
        assert acquire_provider_input(
            selected, resources
        ).frame.location_id.tolist() == ["a"]
        forbidden = Mock(side_effect=AssertionError("Full records fetched"))
        monkeypatch.setattr(resources.get("places"), "resolve", forbidden)
        assert acquire_provider_membership(registered, ids, resources) == set(ids) & {
            "a",
            "b",
        }
        lookup.assert_called_once_with(ids)
        forbidden.assert_not_called()


def test_one_logical_lookup_for_union_and_independent_values() -> None:
    returned = {"b"}

    def lookup(ids: list[str]) -> set[str]:
        assert ids == ["new", "b", "a"]
        ids.clear()
        return returned

    calls = Mock(side_effect=lookup)
    registered, resources = setup(calls)
    additions = ["new", "b"]
    removals = ["b", "a"]
    submitted = additions + removals
    with resources.activate():
        result = acquire_provider_membership(registered, submitted, resources)
    calls.assert_called_once()
    returned.clear()
    assert result == {"b"}
    assert submitted == ["new", "b", "b", "a"]


def test_empty_lookup_does_not_acquire() -> None:
    lookup = Mock(side_effect=AssertionError("Lookup invoked"))
    registered, resources = setup(lookup)
    assert acquire_provider_membership(registered, [], resources) == set()
    assert not resources.instances
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [
        None,
        [],
        ["a"],
        {"a": True},
        frozenset({"a"}),
        {1},
        {None},
        {""},
        {"extra"},
        {np.str_("a")},
    ],
)
def test_invalid_membership_output_is_a_contract_error(output: object) -> None:
    registered, resources = setup(lambda _ids: output)
    with (
        resources.activate(),
        pytest.raises(LocationProviderContractError, match="strict strings"),
    ):
        acquire_provider_membership(registered, ["a"], resources)


@pytest.mark.parametrize("ids", [[1], [None], [""], [b"a"]])
def test_invalid_lookup_ids_fail_before_acquisition(ids: list) -> None:
    registered, resources = setup(Mock())
    with pytest.raises(LocationSelectionError):
        acquire_provider_membership(registered, ids, resources)
    assert not resources.instances


@pytest.mark.parametrize("stage", ["setup", "existing_ids"])
def test_lookup_errors_preserve_cause_and_scope_cleanup(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("lookup failed")
    registered, resources = setup(set)
    monkeypatch.setattr(resources.providers["places"], stage, Mock(side_effect=failure))
    close = Mock()
    monkeypatch.setattr(resources.providers["places"], "close", close)
    with resources.activate():
        with pytest.raises(LocationAcquisitionError) as caught:
            acquire_provider_membership(registered, ["a", "a"], resources)
        assert caught.value.__cause__ is failure
        assert caught.value.input_name == "membership"
        assert caught.value.ids == ("a",)
        close.assert_not_called()
    close.assert_called_once()


def test_resource_configuration_error_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered, resources = setup(Mock())
    failure = ResourceConfigurationError("missing configuration")
    monkeypatch.setattr(resources, "get", Mock(side_effect=failure))
    with pytest.raises(ResourceConfigurationError) as caught:
        acquire_provider_membership(registered, ["a"], resources)
    assert caught.value is failure


def test_missing_membership_does_not_relax_exact_resolution() -> None:
    registered, resources = setup(lambda ids: set(ids) & {"a", "b"})
    with resources.activate():
        assert acquire_provider_membership(registered, ["a", "missing"], resources) == {
            "a"
        }
        selection = PreparedProviderInput(
            "selected", frozenset({"a", "missing"}), registered
        )
        with pytest.raises(LocationProviderContractError, match="match requested IDs"):
            acquire_provider_input(selection, resources)
        provider = cast("LocationProvider", resources.get("places"))
        assert provider.existing_ids([]) == set()
