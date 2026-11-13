from typing import Annotated, cast
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point, Polygon

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.origins import Origin
from lyra.contracts.resources import ResourceConfig, SecretReference
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.provider_acquisition import acquire_provider_input
from lyra.runtime.resources import ResourceScope, collect_resources
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import BaseIO, Resource
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration, frame

CENSUS = Origin(id="census", revision="2025")


class Database(BaseIO):
    def setup(self) -> None:
        self.open = True

    def close(self) -> None:
        self.open = False


class DependentProvider(FakeProvider):
    id = "tracts"
    origins = (CENSUS,)
    schema = declaration()
    database: Annotated[Database, Resource("database")]


def acquisition_fixture(
    resolver: object,
    *,
    schema: pa.DataFrameSchema | None = None,
) -> tuple[PreparedProviderInput, ResourceScope]:
    if isinstance(resolver, Mock):
        resolver = create_autospec(FakeProvider.resolve, side_effect=resolver)
    declared = type(
        "Provider",
        (FakeProvider,),
        {
            "id": "tracts",
            "origins": (CENSUS,),
            "resolve": resolver,
            "secret_names": (),
            "setup": lambda _: None,
            "close": lambda _: None,
            "schema": declaration() if schema is None else schema,
        },
    )
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources={"binding": declared})}
    )
    return (
        PreparedProviderInput("places", frozenset({"a", "b"}), registry["tracts"]),
        ResourceScope(
            {"binding": declared},
            {"binding": ResourceConfig(config={"label": "configured"})},
        ),
    )


@pytest.mark.parametrize("ids", [["a"], ["b"], ["b", "a"], ["a", "b", "a"]])
def test_exact_resolution_and_batch_independent_scope(ids: list[str]) -> None:
    calls = []

    def resolve(_self: object, requested: list[str]) -> gpd.GeoDataFrame:
        calls.append(requested.copy())
        data = frame()
        return data[data.location_id.isin(requested)].iloc[::-1]

    selection, resources = acquisition_fixture(resolve)
    with resources.activate():
        result = acquire_provider_input(selection, resources, needed_ids=ids)
        assert calls == [list(dict.fromkeys(ids))]
        expected = frame().set_index("location_id").loc[sorted(set(ids))]
        pd.testing.assert_frame_equal(
            result.frame.set_index("location_id").sort_index(),
            expected,
            check_like=True,
        )
        assert result.scope == selection.provider.scope
        full = acquire_provider_input(selection, resources)
        assert full.scope == result.scope
        pd.testing.assert_frame_equal(
            full.frame.set_index("location_id").sort_index(),
            frame().set_index("location_id").sort_index(),
            check_like=True,
        )
        assert len(calls) == 2  # Acquisition has no provider record cache.


@pytest.mark.parametrize("ids", [["missing"], ["a", "missing"]])
def test_fixed_dataset_membership_is_independent_of_batch(ids: list[str]) -> None:
    def resolve(_self: object, requested: list[str]) -> gpd.GeoDataFrame:
        data = frame()
        return data[data.location_id.isin(requested)]

    original, resources = acquisition_fixture(resolve)
    selection = PreparedProviderInput(
        "places", frozenset({"a", "b", "missing"}), original.provider
    )
    with resources.activate():
        narrowed = acquire_provider_input(selection, resources, needed_ids=["a"])
        assert narrowed.frame.location_id.tolist() == ["a"]
        with pytest.raises(LocationProviderContractError, match="match requested IDs"):
            acquire_provider_input(selection, resources, needed_ids=ids)
        # An unsuccessful selection does not change the immutable fake dataset.
        repeated = acquire_provider_input(selection, resources, needed_ids=["a"])
        pd.testing.assert_frame_equal(repeated.frame, narrowed.frame)


def test_all_null_nullable_column_survives_acquisition() -> None:
    data = frame()
    data["count"] = pd.Series([None, None], dtype="Int64")
    selection, resources = acquisition_fixture(lambda _self, _ids: data)
    with resources.activate():
        acquired = acquire_provider_input(selection, resources)
        empty = acquire_provider_input(selection, resources, needed_ids=[])
    pd.testing.assert_frame_equal(acquired.frame, data[acquired.frame.columns])
    pd.testing.assert_frame_equal(empty.frame, data.iloc[:0][empty.frame.columns])
    assert acquired.scope == empty.scope == selection.provider.scope


def test_empty_acquires_nothing_and_keeps_native_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("acquired"))
    selection, resources = acquisition_fixture(forbidden)
    monkeypatch.setattr(resources, "get", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    for selected in (
        selection,
        PreparedProviderInput("empty", frozenset(), selection.provider),
    ):
        result = acquire_provider_input(selected, resources, needed_ids=[])
        assert result.scope == selection.provider.scope
        pd.testing.assert_frame_equal(
            result.frame, frame().iloc[:0][result.frame.columns]
        )
    forbidden.assert_not_called()


def test_shared_dependency_configuration_scope_and_caller_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    def setup(self: DependentProvider) -> None:
        assert self.database.open
        assert dict(self.secrets) == {"token": "fixture-token"}
        events.append((self.resource_name, "setup"))

    def close(self: DependentProvider) -> None:
        assert self.database.open
        events.append((self.resource_name, "close"))

    def resolve(self: DependentProvider, ids: list[str]) -> gpd.GeoDataFrame:
        assert self.database.open
        data = frame()
        data["count"] = pd.Series([int(self.config.label)] * 2, dtype="Int64")
        data.attrs["scope"] = "forged"
        return data[data.location_id.isin(ids)]

    tracts = type(
        "Tracts",
        (DependentProvider,),
        {"setup": setup, "close": close, "resolve": resolve},
    )
    counties = type("Counties", (tracts,), {"id": "counties"})
    combined = type(
        "Combined",
        (tracts,),
        {"id": "combined", "origins": (CENSUS, Origin(id="elevation"), CENSUS)},
    )
    definitions = {
        "tract_plugin": PluginDefinition(resources={"tract_binding": tracts}),
        "county_plugin": PluginDefinition(resources={"county_binding": counties}),
        "combined_plugin": PluginDefinition(resources={"combined_binding": combined}),
        "access_plugin": PluginDefinition(resources={"database": Database}),
    }
    registrations = collect_resources(
        {name: definition.resources for name, definition in definitions.items()}
    )
    registry = compile_provider_registry(definitions)
    secret = Mock(return_value="fixture-token")
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", secret)
    configs = {
        key: ResourceConfig(
            config={"label": str(value)},
            secrets={"token": SecretReference(env="FIXTURE_TOKEN")},
        )
        for key, value in (
            ("tract_binding", 10),
            ("county_binding", 20),
            ("combined_binding", 30),
        )
    }
    resources = ResourceScope(registrations, configs)
    with resources.activate():
        results = [
            acquire_provider_input(
                PreparedProviderInput(name, frozenset({"a"}), registry[name]), resources
            )
            for name in ("tracts", "counties", "combined")
        ]
        assert [result.frame["count"].iloc[0] for result in results] == [10, 20, 30]
        assert all(result.frame.location_id.tolist() == ["a"] for result in results)
        assert results[2].scope.origins == frozenset({CENSUS, Origin(id="elevation")})
        assert results[0].scope != results[1].scope
        assert results[0].scope.origins == results[1].scope.origins
        assert results[0].scope == registry["tracts"].scope
        assert cast(
            "DependentProvider", resources.get("tract_binding")
        ).database is resources.get("database")
        assert cast(
            "DependentProvider", resources.get("county_binding")
        ).database is resources.get("database")
        assert cast(
            "DependentProvider", resources.get("combined_binding")
        ).database is resources.get("database")
        assert events == [(name, "setup") for name in configs]
    assert events[3:] == [(name, "close") for name in reversed(configs)]
    assert secret.call_count == 3


@pytest.mark.parametrize(
    "ids", [["a"], ["a", "b", "c"], ["a", "a"], ["", "b"], [None, "b"], [1, 2]]
)
def test_rejects_inexact_or_malformed_returned_ids(ids: list) -> None:
    data = frame().iloc[[0] * len(ids)].copy()
    data["location_id"] = ids
    selection, resources = acquisition_fixture(lambda _self, _ids: data)
    with resources.activate(), pytest.raises(LocationProviderContractError):
        acquire_provider_input(selection, resources)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "duplicate",
        "crs",
        "no_crs",
        "active",
        "type",
        "null",
        "not_frame",
    ],
)
def test_automatic_schema_validation(mutation: str) -> None:
    data = frame()
    match mutation:
        case "extra":
            data["extra"] = 1
        case "missing":
            data = data.drop(columns="count")
        case "duplicate":
            data = pd.concat([data, data[["count"]]], axis=1)
        case "crs":
            data = data.to_crs("EPSG:3857")
        case "no_crs":
            data = gpd.GeoDataFrame(data.to_dict("list"), geometry="geometry")
        case "active":
            data = data.rename_geometry("shape")
        case "type":
            data["count"] = ["one", "two"]
        case "null":
            data.loc[0, "geometry"] = None
        case "not_frame":
            data = pd.DataFrame(data)
    selection, resources = acquisition_fixture(lambda _self, _ids: data)
    with resources.activate(), pytest.raises(LocationProviderContractError) as error:
        acquire_provider_input(selection, resources)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("stage", ["setup", "resolve"])
def test_acquisition_errors_keep_cause_and_scope_ownership(stage: str) -> None:
    failure = RuntimeError("fixture failure")
    selection, resources = acquisition_fixture(lambda _self, _ids: frame())
    setattr(resources.providers["binding"], stage, Mock(side_effect=failure))
    close = Mock()
    resources.providers["binding"].close = close
    with resources.activate():
        with pytest.raises(LocationAcquisitionError) as error:
            acquire_provider_input(selection, resources)
        assert error.value.__cause__ is failure
        assert error.value.input_name == "places"
        assert set(error.value.ids) == {"a", "b"}
        close.assert_not_called()
    close.assert_called_once()


@pytest.mark.parametrize("ids", [["c"], [""], [1], [None]])
def test_invalid_needed_selection_acquires_nothing(ids: list) -> None:
    selection, resources = acquisition_fixture(Mock())
    with pytest.raises(LocationSelectionError):
        acquire_provider_input(selection, resources, needed_ids=ids)
    assert not resources.instances


@pytest.mark.parametrize(
    "geometry", [Point(), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])]
)
def test_geometry_checks_at_acquisition(geometry: Point | Polygon) -> None:
    data = frame()
    data["geometry"] = gpd.GeoSeries([geometry, Point(1, 1)], crs=data.crs)
    selection, resources = acquisition_fixture(lambda _self, _ids: data)
    with resources.activate(), pytest.raises(LocationProviderContractError):
        acquire_provider_input(selection, resources)


@pytest.mark.parametrize("value", [None, -1])
def test_declared_attribute_rules_are_enforced(value: int | None) -> None:
    schema = declaration()
    schema.columns["count"].nullable = False
    schema.columns["count"].checks = [pa.Check.ge(0)]
    data = frame()
    data["count"] = pd.Series([value, 2], dtype="Int64")
    selection, resources = acquisition_fixture(lambda _self, _ids: data, schema=schema)
    with resources.activate(), pytest.raises(LocationProviderContractError):
        acquire_provider_input(selection, resources)
