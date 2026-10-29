from unittest.mock import Mock

import pytest
from lyra.sdk import (
    IOConfig,
    LocationAcquisitionError,
    LocationCollection,
    LocationInput,
    LocationProvider,
    LocationProviderContractError,
    LocationSelection,
    LocationSelectionError,
    ResourceConfig,
    SecretReference,
    UnresolvedLocationIDsError,
    resolve_locations,
)
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from lyra.sdk.models.location import ResolvedLocation
from lyra.sdk.resources import ResourceConfigurationError

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


def features(ids: list[str], crs: str = "EPSG:4326") -> LocationInput:
    return LocationInput.model_validate(
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


class Provider(LocationProvider[IOConfig]):
    collections = COLLECTIONS

    def resolve(self, collection: str, unit_type: str, ids: list[str]) -> LocationInput:
        assert unit_type in {"lot", "block", "road"}
        crs = next(item.crs for item in self.collections if item.name == collection)
        return features(list(reversed(ids)), crs)


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(Provider, "setup", lambda _: events.append("setup"))
    monkeypatch.setattr(Provider, "close", lambda _: events.append("close"))
    return events


def resolve(inputs: dict[str, LocationSelection]) -> dict[str, ResolvedLocation]:
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
    ) -> LocationInput:
        return spy(self, collection, unit_type, ids)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Provider, "resolve", tracked)
        result = resolve(inputs)
    assert lifecycle == ["setup", "close"]
    assert spy.call_count == 3
    assert spy.call_args_list[0].args[1:] == ("test:units", "lot", ["001", " a/b "])
    assert list(result) == list(inputs)
    assert [f.id for f in result["lots"].location.features] == ["001", " a/b "]
    assert inputs["lots"].ids == ["001", " a/b ", "001"]
    assert result["blocks"].unit_type == "block"
    assert result["roads"].collection == "test:roads"
    assert result["roads"].location.crs.properties.name == "EPSG:27700"
    assert result["lots"].location.features[0].properties == {"original": "001"}
    assert result["lots"].location is not result["blocks"].location


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
        (features(["a"]), ("b",)),
        (features(["a", "b", "extra"]), ("extra",)),
        (features(["a", "b", "a"]), ("a",)),
        (features(["a", "b"], "EPSG:27700"), ("a", "b")),
        (features(["a"]).model_copy(update={"features": []}), ("a", "b")),
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
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=features(["wrong"])))

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
    assert result["lots"].location.features[0].id == "a"
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
            features(["a"]),
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
    invalid = features(["a"])
    invalid.features[0].geometry = invalid.features[0].geometry.model_copy(
        update={"coordinates": [1.0]}
    )
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=invalid))
    with pytest.raises(LocationProviderContractError):
        resolve({"lots": selection("a")})

    def invalid_model(*_args: object) -> LocationInput:
        return LocationInput.model_validate({})

    monkeypatch.setattr(Provider, "resolve", invalid_model)
    with pytest.raises(LocationProviderContractError):
        resolve({"lots": selection("a")})
    assert lifecycle == ["setup", "close", "setup", "close"]


def test_provider_feature_ids_must_be_strings_without_coercion(
    monkeypatch: pytest.MonkeyPatch, lifecycle: list[str]
) -> None:
    invalid = features(["a"])
    invalid.features[0] = invalid.features[0].model_copy(update={"id": b"a"})
    monkeypatch.setattr(Provider, "resolve", Mock(return_value=invalid))
    with (
        pytest.warns(
            UserWarning,
            match=(
                r"PydanticSerializationUnexpectedValue\(Expected `str`"
                r".*field_name='id'"
            ),
        ),
        pytest.raises(LocationProviderContractError) as caught,
    ):
        resolve({"lots": selection("a")})
    assert caught.value.input_name == "lots"
    assert caught.value.ids == ("a",)
    assert lifecycle == ["setup", "close"]
