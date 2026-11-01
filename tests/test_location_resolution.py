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
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    ResourceConfig,
    SecretReference,
    UnresolvedLocationIDsError,
    location_operation,
    metric,
    resolve_locations,
)
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.location_contracts import LocationContract, LocationFamily
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from lyra.sdk.models.location_payload import LocationPayload
from lyra.sdk.preparation import prepare_execution
from lyra.sdk.resources import ResourceConfigurationError, ResourceScope
from lyra.sdk.revisions import RevisionBinding, RevisionPlan
from pydantic import Field
from shapely.geometry import Point

from tests.location_helpers import (
    fixture_locations,
    node_contract,
    output_schema,
    producer_result,
    scoped_payload,
)

COLLECTIONS = (
    LocationCollection(name="test:units", unit_types=("lot", "block"), crs="EPSG:4326"),
    LocationCollection(name="test:roads", unit_types=("road",), crs="EPSG:27700"),
)


def selection(
    *ids: str, collection: str = "test:units", unit_type: str = "lot"
) -> LocationSelection:
    return LocationSelection(collection=collection, unit_type=unit_type, ids=list(ids))


def features(ids: list[str], crs: str = "EPSG:4326") -> Locations:
    return fixture_locations(
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
        ),
        schema=output_schema(original=str),
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
    contracts = tuple(
        LocationContract(
            family=LocationFamily.from_schema(collection, unit, schema),
            source="fixture",
            geographical_edition="current",
            meaning="Provider locations",
        )
        for (collection, unit), schema in outputs.items()
    )

    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        collection, unit_type = contract.family.collection, contract.family.unit_type
        assert unit_type in {"lot", "block", "road"}
        crs = next(item.crs for item in self.collections if item.name == collection)
        return producer_result(
            fixture_locations(
                features(list(reversed(ids)), crs).to_dict()
                | {"collection": collection, "unit_type": unit_type},
                schema=output_schema(original=str),
            ),
            self.outputs[collection, unit_type],
        )


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))
    return events


SELECTED = SelectedLocationProvider(
    "test-plugin",
    LocationProviderManifest(
        resource="locations", collections=COLLECTIONS, contracts=Provider.contracts
    ),
)


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
        self: Provider, contract: LocationContract, ids: list[str]
    ) -> Locations:
        return spy(self, contract, ids)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Provider, "resolve", tracked)
        result = resolve(inputs)
    assert lifecycle == ["setup", "close"]
    assert spy.call_count == 3
    assert spy.call_args_list[0].args[1:] == (Provider.contracts[0], ["001", " a/b "])
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

    def invalid_model(*_args: object) -> LocationPayload:
        return LocationPayload.model_validate(scoped_payload({}))

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
        locations={"lots": node_contract()},
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
    empty = fixture_locations(
        normalized.to_dict() | {"features": []}, schema=output_schema(original=str)
    )
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
        contracts = tuple(
            LocationContract(
                family=LocationFamily.from_schema(collection, unit, schema),
                source="fixture",
                geographical_edition="current",
                meaning="Provider locations",
            )
            for (collection, unit), schema in outputs.items()
        )

        def resolve(
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            _collection, _unit_type = (
                contract.family.collection,
                contract.family.unit_type,
            )
            value = super().resolve(contract, ids)
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


def test_concrete_editions_preserve_source_and_contents() -> None:
    first = Provider.contracts[0]
    second = first.model_copy(
        update={
            "source": "second",
            "edition": "2025",
            "data_revision": "r2",
            "observation_period": "2025",
        }
    )

    class Editions(Provider):
        contracts = (*Provider.contracts, second)

        def resolve(
            self, contract: LocationContract, ids: list[str]
        ) -> gpd.GeoDataFrame:
            frame = super().resolve(contract, ids)
            if contract.reference == second.reference:
                frame["original"] = ["second observation"] * len(frame)
                frame.geometry = [Point(3, 4)] * len(frame)
            return frame

    manifest = LocationProviderManifest(
        resource="locations",
        collections=Editions.collections,
        contracts=Editions.contracts,
    )
    assert (
        LocationProviderManifest.model_validate_json(manifest.model_dump_json())
        == manifest
    )
    selected = SelectedLocationProvider("test", manifest)
    scope = ResourceScope({"locations": Editions})
    resolver = LocationResolver(scope, selected=selected, provider=Editions)
    with scope.activate():
        with pytest.raises(LocationSelectionError, match="exactly one"):
            resolver.resolve({"lots": selection("a")})
        assert scope.instances == {}
        with pytest.raises(LocationSelectionError, match="exactly one"):
            resolver.resolve(
                {"lots": selection("a")},
                contracts={
                    "lots": first.reference.model_copy(update={"source": "unknown"})
                },
            )
        assert scope.instances == {}
        values = [
            resolver.resolve(
                {"lots": selection("a")}, contracts={"lots": item.reference}
            )["lots"]
            for item in (first, second)
        ]
    assert values[0].contract == first.reference
    assert values[1].contract == second.reference
    assert values[1].data_revision == "r2"
    assert values[0].to_features()[0].properties == {"original": "a"}
    assert values[1].to_features()[0].properties == {"original": "second observation"}
    assert values[0].to_geodataframe().geometry.iloc[0] == Point(1, 2)
    assert values[1].to_geodataframe().geometry.iloc[0] == Point(3, 4)


def test_conflicting_contract_registration_fails() -> None:
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        type(
            "Duplicate",
            (Provider,),
            {"contracts": (*Provider.contracts, Provider.contracts[0])},
        )
    with pytest.raises(PluginDefinitionError, match="at least 1"):
        type("Missing", (Provider,), {"contracts": ()})


@pytest.mark.parametrize("revision", [None, "older"])
@pytest.mark.parametrize("reject", [False, True])
def test_pinned_provider_revision(
    revision: str | None,
    *,
    reject: bool,
    lifecycle: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    contract = Provider.contracts[0]
    seen = []

    def acquire(
        _self: Provider, requested: LocationContract, ids: list[str]
    ) -> gpd.GeoDataFrame:
        seen.append(requested.data_revision)
        if reject:
            message = "Unavailable revision"
            raise ValueError(message)
        return frame_features(list(reversed(ids)))

    monkeypatch.setattr(Provider, "resolve", acquire)
    scope = ResourceScope({"locations": Provider})
    resolver = LocationResolver(
        scope,
        selected=SELECTED,
        provider=Provider,
        revisions=RevisionPlan(
            frozenset(), frozenset({RevisionBinding(contract.reference, revision)})
        ),
    )
    with scope.activate():
        if reject:
            with pytest.raises(LocationAcquisitionError):
                resolver.resolve({"lots": selection("b", "a", "b")})
        else:
            value = resolver.resolve({"lots": selection("b", "a", "b")})["lots"]
            assert value.ids == ["b", "a"]
            assert value.contract == contract.reference
            assert value.data_revision == revision
    assert seen == [revision]
    assert lifecycle == ["setup", "close"]
    assert contract.data_revision is None


@pytest.mark.parametrize("returned", [["a"], []])
def test_partial_provider_rows_report_missing(
    returned: list[str], monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    monkeypatch.setattr(
        Provider,
        "resolve",
        Mock(
            return_value=frame_features(returned)
            if returned
            else Provider.outputs["test:units", "lot"].empty_frame("EPSG:4326")
        ),
    )
    with pytest.raises(UnresolvedLocationIDsError) as caught:
        resolve({"lots": selection("a", "b")})
    assert caught.value.ids == tuple(i for i in ["a", "b"] if i not in returned)
    assert caught.value.input_name == "lots"
    assert caught.value.collection == "test:units"
    assert lifecycle == ["setup", "close"]


def test_executor_acquires_prepared_sources(
    lifecycle: list[str], tmp_path: Path
) -> None:
    @metric(
        locations={"lots": node_contract()},
        name="pinned",
        description="Pinned",
        outputs={"result": pa.DataFrameSchema({})},
    )
    def consume(lots: Locations) -> pd.DataFrame:
        return pd.DataFrame(index=range(len(lots.ids)))

    plugin = PluginDefinition(
        metrics=[consume],
        resources={"locations": Provider},
        location_provider="locations",
    )
    runner = LocalRunner.from_definitions({"test": plugin}, cache_dir=tmp_path)
    prepared = prepare_execution(
        runner.catalog,
        {"metric": "pinned", "inputs": {"lots": selection("b", "a", "b")}},
    )
    assert lifecycle == []
    executor = GraphExecutor(
        runner.catalog.graph,
        {"test": plugin},
        Mock(),
        location_provider=runner.catalog.location_provider,
    )
    values = executor.acquire_sources(prepared)
    assert values["lots"].ids == ["b", "a"]
    assert values["lots"].contract == prepared.inputs[0].binding.source
    assert lifecycle == ["setup", "close"]
    assert len(executor.execute(prepared)["result"]) == 2
    assert lifecycle == ["setup", "close", "setup", "close"]
    runner.catalog.graph.nodes[prepared.node].node.location_contracts[
        "lots"
    ].data_revision = "changed"
    with pytest.raises(ValueError, match=r"Conflicting|pinned"):
        executor.acquire_sources(prepared)
    with pytest.raises(GraphExecutionError, match=r"Conflicting|pinned"):
        executor.execute(prepared)
    assert lifecycle == ["setup", "close", "setup", "close"]


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_prepared_routes_share_batches(
    lifecycle: list[str], tmp_path: Path, *, empty: bool, fail: bool
) -> None:
    class Parameters(MetricParameters):
        count: int = Field(default=2, description="Selector count")

    source = node_contract()
    middle = node_contract("test:middle", "point", output_schema(original=str))
    target = node_contract("test:target", "point", output_schema(original=str))
    calls = []

    @location_operation(
        name="first",
        description="First",
        canonical=True,
        locations={"source": source},
        output_contract=middle,
        output=output_schema(original=str),
    )
    def first(source: Locations, parameters: Parameters) -> gpd.GeoDataFrame:
        assert parameters.count == 2
        if fail:
            message = "Selected route failed"
            raise ValueError(message)
        calls.append(("first", source.ids))
        return (
            output_schema(original=str).empty_frame("EPSG:4326")
            if empty
            else frame_features(["z", "a", "z"])
        )

    @location_operation(
        name="second",
        description="Second",
        canonical=True,
        locations={"source": middle},
        output_contract=target,
        output=output_schema(original=str),
    )
    def second(source: Locations) -> gpd.GeoDataFrame:
        calls.append(("second", source.ids))
        return source.to_geodataframe()

    detour = node_contract("test:detour", "point", output_schema(original=str))

    @location_operation(
        name="alternative",
        description="Alternative",
        canonical=True,
        locations={"source": source},
        output_contract=detour,
        output=output_schema(original=str),
    )
    def alternative(source: Locations) -> gpd.GeoDataFrame:
        calls.append(("alternative", source.ids))
        return source.to_geodataframe()

    @location_operation(
        name="return_from_detour",
        description="Return",
        canonical=True,
        locations={"source": detour},
        output_contract=middle,
        output=output_schema(original=str),
    )
    def return_from_detour(source: Locations) -> gpd.GeoDataFrame:
        calls.append(("return", source.ids))
        return source.to_geodataframe()

    @metric(
        name="routed",
        description="Routed",
        locations={"left": target, "right": target},
        outputs={
            "result": pa.DataFrameSchema(
                {
                    "count": pa.Column(
                        int, description="Count", metadata={"unit": "count"}
                    )
                }
            )
        },
    )
    def consume(left: Locations, right: Locations) -> pd.DataFrame:
        assert left.ids == right.ids == ([] if empty else ["a", "z"])
        assert lifecycle == ["setup"]
        return pd.DataFrame({"count": [len(left) + len(right)]})

    plugin = PluginDefinition(
        metrics=[consume],
        location_operations=[first, second, alternative, return_from_detour],
        resources={"locations": Provider},
        location_provider="locations",
    )
    runner = LocalRunner.from_definitions({"test": plugin}, cache_dir=tmp_path)
    prepared = prepare_execution(
        runner.catalog,
        {
            "metric": "routed",
            "inputs": {name: selection("b", "a") for name in ("left", "right")},
        },
    )
    executor = GraphExecutor(
        runner.catalog.graph,
        {"test": plugin},
        Mock(),
        location_provider=runner.catalog.location_provider,
    )
    if fail:
        with pytest.raises(GraphExecutionError, match="Selected route failed"):
            executor.execute(prepared)
        assert calls == []
        assert lifecycle == ["setup", "close"]
        return
    result = executor.execute(prepared)
    assert result["result"]["count"].tolist() == [0 if empty else 4]
    assert calls == [("first", ["b", "a"]), ("second", [] if empty else ["a", "z"])]
    assert lifecycle == ["setup", "close"]
