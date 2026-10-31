from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    IOConfig,
    LocalRunner,
    LocationAcquisitionError,
    LocationCollection,
    LocationContextAdapter,
    LocationOutputSchema,
    LocationProvider,
    LocationProviderContractError,
    LocationResolver,
    Locations,
    LocationSelection,
    LocationSelectionError,
    PluginDefinition,
    ResourceConfig,
    SecretReference,
    UnresolvedLocationIDsError,
    metric,
    resolve_locations,
)
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from lyra.sdk.resources import ResourceConfigurationError, ResourceScope
from shapely.geometry import Point

from tests.location_helpers import output_schema, producer_result, scoped_payload

COLLECTIONS = (
    LocationCollection(name="test:units", unit_types=("lot", "block"), crs="EPSG:4326"),
    LocationCollection(name="test:roads", unit_types=("road",), crs="EPSG:27700"),
)
SELECTED = SelectedLocationProvider(
    "test-plugin",
    LocationProviderManifest(resource="locations", collections=COLLECTIONS),
)


def selection(
    *ids: str, collection: str = "test:units", unit_type: str = "lot"
) -> LocationSelection:
    return LocationSelection(collection=collection, unit_type=unit_type, ids=list(ids))


def features(ids: list[str], crs: str = "EPSG:4326") -> Locations:
    return Locations.from_dict(
        scoped_payload(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": unit_id,
                        "geometry": {"type": "Point", "coordinates": [1, 2]},
                        "properties": {"original": unit_id},
                    }
                    for unit_id in ids
                ],
                "crs": {"type": "name", "properties": {"name": crs}},
            }
        )
    )


def frame_features(ids: list[str], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ids, "original": ids}, geometry=[Point(1, 2)] * len(ids), crs=crs
    )


class Provider(LocationProvider[IOConfig]):
    collections = COLLECTIONS
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        (item.name, unit): output_schema(original=str)
        for item in COLLECTIONS
        for unit in item.unit_types
    }

    def resolve(
        self, collection: str, unit_type: str, ids: list[str]
    ) -> gpd.GeoDataFrame:
        assert unit_type in {"lot", "block", "road"}
        crs = next(item.crs for item in self.collections if item.name == collection)
        return producer_result(
            Locations.from_dict(
                features(list(reversed(ids)), crs).to_dict()
                | {"collection": collection, "unit_type": unit_type}
            ),
            self.outputs[collection, unit_type],
        )


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))
    return events


def resolve(inputs: dict[str, LocationSelection]) -> dict[str, Locations]:
    return resolve_locations(
        inputs, selected=SELECTED, resources={"locations": Provider}
    )


def test_multiple_inputs_share_lifecycle_and_preserve_scope_and_order(
    lifecycle: list[str],
) -> None:
    inputs = {
        "lots": selection("001", " a/b ", "001"),
        "blocks": selection("001", unit_type="block"),
        "roads": selection("r", collection="test:roads", unit_type="road"),
    }
    original = Provider.resolve
    spy = Mock(wraps=original)

    def tracked(
        self: Provider, collection: str, unit_type: str, ids: list[str]
    ) -> Locations:
        return spy(self, collection, unit_type, ids)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Provider, "resolve", tracked)
        result = resolve(inputs)
    assert lifecycle == ["setup", "close"]
    assert spy.call_count == 3
    assert spy.call_args_list[0].args[1:] == ("test:units", "lot", ["001", " a/b "])
    assert list(result) == list(inputs)
    assert [f.id for f in result["lots"].to_features()] == ["001", " a/b "]
    assert inputs["lots"].ids == ["001", " a/b ", "001"]
    assert result["blocks"].unit_type == "block"
    assert result["roads"].collection == "test:roads"
    assert result["roads"].crs.properties.name == "EPSG:27700"
    assert result["lots"].to_features()[0].properties == {"original": "001"}
    assert result["lots"] is not result["blocks"]


@pytest.mark.parametrize(
    "invalid",
    [
        selection("a", collection="test:unknown"),
        selection("a", unit_type="unknown"),
        selection("a").model_copy(update={"ids": []}),
        selection("a").model_copy(update={"ids": [""]}),
        selection("a").model_copy(update={"collection": "not-namespaced"}),
        selection("a").model_copy(update={"unit_type": ""}),
    ],
)
def test_all_inputs_validated_before_setup(
    invalid: LocationSelection, lifecycle: list[str]
) -> None:
    with pytest.raises(LocationSelectionError) as caught:
        resolve({"valid": selection("a"), "bad": invalid})
    assert caught.value.input_name == "bad"
    assert caught.value.ids == tuple(invalid.ids)
    assert lifecycle == []


def test_no_inputs_need_no_provider_or_resources(lifecycle: list[str]) -> None:
    assert resolve_locations({}, selected=None, resources={}) == {}
    assert lifecycle == []
    with pytest.raises(ResourceConfigurationError):
        resolve_locations({"lots": selection("a")}, selected=None, resources={})


@pytest.mark.parametrize("missing", [["b"], ["a", "b"]])
def test_unresolved_ids_are_named_and_never_partial(
    missing: list[str], monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    monkeypatch.setattr(
        Provider,
        "resolve",
        Mock(side_effect=UnresolvedLocationIDsError("test:units", "lot", missing)),
    )
    with pytest.raises(UnresolvedLocationIDsError) as caught:
        resolve({"lots": selection("a", "b")})
    assert caught.value.input_name == "lots"
    assert caught.value.ids == tuple(missing)
    assert caught.value.collection == "test:units"
    assert lifecycle == ["setup", "close"]


@pytest.mark.parametrize(
    ("output", "ids"),
    [
        (frame_features(["a"]), ("b",)),
        (frame_features(["a", "b", "extra"]), ("extra",)),
        (frame_features(["a", "b", "a"]), ("a",)),
        (frame_features(["a", "b"], "EPSG:27700"), ("a", "b")),
        (frame_features([]), ("a", "b")),
        ({"type": "FeatureCollection"}, ("a", "b")),
        (None, ("a", "b")),
    ],
)
def test_invalid_output_is_a_contract_violation(
    output: object,
    ids: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: list[str],
) -> None:
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=output))
    with pytest.raises(LocationProviderContractError) as caught:
        resolve({"lots": selection("a", "b")})
    assert caught.value.input_name == "lots"
    assert caught.value.ids == ids
    assert lifecycle == ["setup", "close"]


@pytest.mark.parametrize("stage", ["setup", "resolve", "close"])
def test_lifecycle_failures_do_not_return_results(
    stage: str, monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    def fail(*_args: object) -> None:
        if stage != "resolve":
            lifecycle.append(stage)
        message = "password=private"
        raise OSError(message)

    monkeypatch.setattr(Provider, stage, fail)
    with pytest.raises(LocationAcquisitionError) as caught:
        resolve({"lots": selection("a")})
    assert "private" not in str(caught.value)
    assert lifecycle == ["setup", "close"]
    if stage == "resolve":
        assert caught.value.input_name == "lots"
        assert caught.value.ids == ("a",)


def test_cleanup_does_not_mask_resolution_error(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    monkeypatch.setattr(
        Provider, "resolve", Mock(return_value=frame_features(["wrong"]))
    )

    def close(_self: Provider) -> None:
        lifecycle.append("close")
        message = "cleanup failed"
        raise OSError(message)

    monkeypatch.setattr(Provider, "close", close)
    with pytest.raises(LocationProviderContractError):
        resolve({"lots": selection("a")})
    assert lifecycle == ["setup", "close"]


def test_configuration_and_fake_override(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    class Config(IOConfig):
        label: str

    class Override(Provider):
        config_model = Config
        secret_names = ("token",)

        def setup(self) -> None:
            assert isinstance(self.config, Config)
            assert self.config.label == "configured"
            assert dict(self.secrets) == {"token": "fake-token"}
            super().setup()

    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    result = resolve_locations(
        {"lots": selection("a")},
        selected=SELECTED,
        resources={"locations": Provider},
        overrides={"locations": Override},
        configuration={
            "locations": ResourceConfig(
                config={"label": "configured"},
                secrets={"token": SecretReference(env="LOCATION_TEST_TOKEN")},
            )
        },
    )
    assert result["lots"].to_features()[0].id == "a"
    assert lifecycle == ["setup", "close"]


@pytest.mark.parametrize(
    "error",
    [
        UnresolvedLocationIDsError("test:other", "lot", ["a"]),
        UnresolvedLocationIDsError("test:units", "block", ["a"]),
        UnresolvedLocationIDsError("test:units", "lot", []),
        UnresolvedLocationIDsError("test:units", "lot", ["extra"]),
        UnresolvedLocationIDsError("test:units", "lot", ["a", "a"]),
    ],
)
def test_invalid_unresolved_reports_are_contract_errors(
    error: UnresolvedLocationIDsError,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: list[str],
) -> None:
    monkeypatch.setattr(Provider, "resolve", Mock(side_effect=error))
    with pytest.raises(LocationProviderContractError) as caught:
        resolve({"lots": selection("a")})
    assert caught.value.input_name == "lots"
    assert lifecycle == ["setup", "close"]


def test_later_input_failure_closes_scope_without_partial_result(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    response = Mock(
        side_effect=[
            frame_features(["a"]),
            UnresolvedLocationIDsError("test:units", "block", ["b"]),
        ]
    )
    monkeypatch.setattr(Provider, "resolve", response)
    with pytest.raises(UnresolvedLocationIDsError) as caught:
        resolve({"lots": selection("a"), "blocks": selection("b", unit_type="block")})
    assert caught.value.input_name == "blocks"
    assert caught.value.ids == ("b",)
    assert response.call_count == 2
    assert lifecycle == ["setup", "close"]


def test_malformed_geometry_and_validation_errors(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    invalid = frame_features(["a"])
    invalid.loc[0, "geometry"] = None
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=invalid))
    with pytest.raises(LocationProviderContractError):
        resolve({"lots": selection("a")})

    def invalid_model(*_args: object) -> Locations:
        return Locations.from_dict(scoped_payload({}))

    monkeypatch.setattr(Provider, "resolve", invalid_model)
    with pytest.raises(LocationProviderContractError):
        resolve({"lots": selection("a")})
    assert lifecycle == ["setup", "close", "setup", "close"]


def test_provider_feature_ids_must_be_strings_without_coercion(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    invalid = frame_features(["a"])
    invalid["id"] = [b"a"]
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=invalid))
    with pytest.raises(LocationProviderContractError) as caught:
        resolve({"lots": selection("a")})
    assert caught.value.input_name == "lots"
    assert caught.value.ids == ("a",)
    assert lifecycle == ["setup", "close"]


def test_provider_scope_comes_from_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=frame_features(["a"])))
    value = resolve({"lots": selection("a")})["lots"]
    assert (value.collection, value.unit_type) == ("test:units", "lot")


def test_provider_coercion_reaches_handlers_and_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    schema = output_schema(original=int).schema
    schema.columns["original"].coerce = True
    contracts = {scope: LocationOutputSchema(schema) for scope in Provider.outputs}
    monkeypatch.setattr(Provider, "outputs", contracts)
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=frame_features(["1"])))
    normalized = resolve({"lots": selection("1")})["lots"]
    assert normalized.to_features()[0].properties == {"original": 1}
    raw = features(["1"])
    assert (
        PreparedResolvedInvocation("consume", None, {"lots": normalized}).identity
        != PreparedResolvedInvocation("consume", None, {"lots": raw}).identity
    )

    @metric(
        name="consume",
        description="Inspect normalized inputs",
        outputs={"result": pa.DataFrameSchema({})},
    )
    def consume(lots: Locations) -> pd.DataFrame:
        assert lots == normalized
        assert type(lots.to_features()[0].properties["original"]) is int
        return pd.DataFrame()

    plugin = PluginDefinition(
        metrics=[consume],
        resources={"locations": Provider},
        location_provider="locations",
    )
    runner = LocalRunner.from_definitions({"test": plugin}, cache_dir=tmp_path)
    runner.run("consume", {"lots": selection("1")})
    empty = Locations.from_dict(normalized.to_dict() | {"features": []})
    invocation = plugin.prepare_resolved_invocation("consume", {"lots": empty})
    assert invocation.arguments()["lots"] == empty


@pytest.mark.parametrize("properties", [{}, {"original": "a", "extra": 1}])
def test_provider_properties_must_match_declared_schema(
    properties: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = frame_features(["a"])
    value = source.drop(columns="original").assign(**properties)
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=value))
    with pytest.raises(LocationProviderContractError, match="schema"):
        resolve({"lots": selection("a")})


def test_override_uses_registered_schema() -> None:
    class Override(Provider):
        outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
            scope: output_schema() for scope in Provider.outputs
        }

        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            value = super().resolve(collection, unit_type, ids)
            return value.drop(columns="original")

    with pytest.raises(LocationProviderContractError, match="schema"):
        resolve_locations(
            {"lots": selection("a")},
            selected=SELECTED,
            resources={"locations": Provider},
            overrides={"locations": Override},
        )


def test_borrowed_scope_reuses_provider_and_owner_closes_after_failure(
    lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = ResourceScope({"locations": Provider})
    resolver = LocationResolver(scope, selected=SELECTED, provider=Provider)
    assert lifecycle == []
    with scope.activate():
        first = resolver.resolve_locations("test:units", "lot", ["b", "a", "b"])
        instance = scope.instances["locations"]
        second = resolver.resolve({"lots": selection("b", "a")})["lots"]
        assert first == second
        assert first is not second
        assert first.ids == ["b", "a"]
        assert scope.instances["locations"] is instance
        assert lifecycle == ["setup"]
        monkeypatch.setattr(
            Provider, "resolve", Mock(return_value=frame_features(["wrong"]))
        )
        with pytest.raises(LocationProviderContractError):
            resolver.resolve_locations("test:units", "lot", ["a"])
        assert lifecycle == ["setup"]
    assert lifecycle == ["setup", "close"]
    assert scope.instances == {}


@pytest.mark.parametrize("unit_type", ["lot", "block", "road"])
def test_empty_lookup_validates_without_provider_acquisition(
    unit_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = "test:roads" if unit_type == "road" else "test:units"
    scope = ResourceScope({"locations": Provider})
    resolver = LocationResolver(scope, selected=SELECTED, provider=Provider)
    for method in ["__init__", "setup", "resolve", "close"]:
        monkeypatch.setattr(Provider, method, Mock(side_effect=AssertionError(method)))
    with scope.activate():
        value = resolver.resolve_locations(collection, unit_type, [])
        assert value.ids == []
        assert value.collection == collection
        assert value.unit_type == unit_type
        assert value.crs.properties.name == (
            "EPSG:27700" if unit_type == "road" else "EPSG:4326"
        )
        assert scope.instances == {}
        with pytest.raises(LocationSelectionError):
            resolver.resolve_locations("test:missing", unit_type, [])
        schema = output_schema(original=str).schema
        schema.checks = [pa.Check(lambda frame: len(frame) > 0, name="nonempty")]
        monkeypatch.setattr(
            Provider,
            "outputs",
            {key: LocationOutputSchema(schema) for key in Provider.outputs},
        )
        with pytest.raises(LocationProviderContractError, match="schema"):
            resolver.resolve_locations(collection, unit_type, [])


@pytest.mark.parametrize("stage", ["setup", "resolve"])
def test_borrowed_failure_retains_input_node_and_safe_message(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: list[str],
) -> None:
    monkeypatch.setattr(Provider, stage, Mock(side_effect=OSError("password=private")))
    scope = ResourceScope({"locations": Provider})
    resolver = LocationResolver(scope, selected=SELECTED, provider=Provider)
    with pytest.raises(LocationAcquisitionError) as caught, scope.activate():
        resolver.resolve_locations(
            "test:units", "lot", ["a"], input_name="lots", node="select"
        )
    assert caught.value.input_name == "lots"
    assert caught.value.node == "select"
    assert caught.value.ids == ("a",)
    assert "private" not in str(caught.value)
    assert "select" in str(caught.value.__notes__)
    assert lifecycle[-1] == "close"


@pytest.mark.parametrize(
    "output",
    [
        frame_features(["wrong"]),
        frame_features(["a", "b"], "EPSG:27700"),
        frame_features(["a", "a"]),
        None,
    ],
)
def test_context_and_ordinary_lookups_share_validation(
    output: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=output))
    scope = ResourceScope({"locations": Provider})
    context = LocationContextAdapter(
        Mock(node="select"),
        LocationResolver(scope, selected=SELECTED, provider=Provider),
    )
    with pytest.raises(LocationProviderContractError) as ordinary:
        resolve({"lots": selection("a", "b", "a")})
    with scope.activate(), pytest.raises(LocationProviderContractError) as caught:
        context.resolve_locations(
            "test:units", "lot", ["a", "b", "a"], input_name="lots"
        )
    assert str(caught.value) == str(ordinary.value)
    assert caught.value.ids == ordinary.value.ids
    assert caught.value.input_name == "lots"
    assert caught.value.node == "select"


@pytest.mark.parametrize("ids", [[""], ["a"]])
def test_context_selection_and_unresolved_diagnostics(
    ids: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Provider,
        "resolve",
        Mock(side_effect=UnresolvedLocationIDsError("test:units", "lot", ["a"])),
    )
    scope = ResourceScope({"locations": Provider})
    context = LocationContextAdapter(
        Mock(node="select"),
        LocationResolver(scope, selected=SELECTED, provider=Provider),
    )
    error_type = LocationSelectionError if ids == [""] else UnresolvedLocationIDsError
    with scope.activate(), pytest.raises(error_type) as caught:
        context.resolve_locations("test:units", "lot", ids, input_name="lots")
    assert caught.value.node == "select"
    assert caught.value.input_name == "lots"
    assert caught.value.ids == tuple(ids)


@pytest.mark.parametrize("ids", [None, 42, "a"])
def test_malformed_lookup_ids_preserve_selection_diagnostics(ids: list[str]) -> None:
    scope = ResourceScope({"locations": Provider})
    context = LocationContextAdapter(
        Mock(node="select"),
        LocationResolver(scope, selected=SELECTED, provider=Provider),
    )
    with scope.activate(), pytest.raises(LocationSelectionError) as caught:
        context.resolve_locations("test:units", "lot", ids, input_name="lots")
    assert caught.value.node == "select"
    assert caught.value.input_name == "lots"
    assert caught.value.ids == ()
    assert scope.instances == {}


@pytest.mark.parametrize("missing", ["selected", "provider"])
def test_missing_provider_configuration_retains_receiving_context(missing: str) -> None:
    scope = ResourceScope({})
    context = LocationContextAdapter(
        Mock(node="select"),
        LocationResolver(
            scope,
            selected=None if missing == "selected" else SELECTED,
            provider=None if missing == "provider" else Provider,
        ),
    )
    with pytest.raises(ResourceConfigurationError) as caught:
        context.resolve_locations("test:units", "lot", [], input_name="lots")
    assert "select" in str(caught.value.__notes__)
    assert "lots" in str(caught.value.__notes__)
    assert scope.instances == {}
