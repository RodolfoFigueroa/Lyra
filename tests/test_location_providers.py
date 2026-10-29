import logging
from typing import ClassVar
from unittest.mock import Mock

import pytest
from lyra.sdk import (
    IOConfig,
    LocationAcquisitionError,
    LocationCollection,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    PluginDefinitionError,
    ResourceConfig,
    SecretReference,
    UnresolvedLocationIDsError,
)
from lyra.sdk.resources import ResourceScope, resource_metadata
from pydantic import ValidationError

from tests.location_helpers import output_schema, scoped_payload

COLLECTION = LocationCollection(
    name="observatory:features", unit_types=("sensor", "road"), crs="EPSG:27700"
)


class ProviderSettings(IOConfig):
    label: str


class FakeProvider(LocationProvider[ProviderSettings]):
    config_model = ProviderSettings
    secret_names = ("token",)
    collections = (COLLECTION,)
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        (COLLECTION.name, unit): output_schema(label=str)
        for unit in COLLECTION.unit_types
    }
    events: ClassVar[list[str]] = []

    def setup(self) -> None:
        assert dict(self.secrets) == {"token": "fake-token"}
        self.events.append("setup")

    def close(self) -> None:
        self.events.append("close")

    def resolve(self, collection: str, unit_type: str, ids: list[str]) -> Locations:
        with self.operation("resolve"):
            missing = [unit_id for unit_id in ids if unit_id not in {"001", "a/b"}]
            if missing:
                raise UnresolvedLocationIDsError(collection, unit_type, missing)
            return Locations.model_validate(
                scoped_payload(
                    {
                        "collection": collection,
                        "unit_type": unit_type,
                        "features": [
                            {
                                "type": "Feature",
                                "id": unit_id,
                                "geometry": {"type": "Point", "coordinates": [1, 2]},
                                "properties": {"label": self.config.label},
                            }
                            for unit_id in ids
                        ],
                        "crs": {"type": "name", "properties": {"name": COLLECTION.crs}},
                    }
                )
            )


def _scope(provider: type[FakeProvider] = FakeProvider) -> ResourceScope:
    return ResourceScope(
        {"locations": provider},
        {
            "locations": ResourceConfig(
                config={"label": "station"},
                secrets={"token": SecretReference(env="LOCATION_TEST_TOKEN")},
            )
        },
    )


def test_metadata_inspection_never_acquires_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("Inspection acquired a resource"))
    monkeypatch.setattr(FakeProvider, "__init__", forbidden)
    monkeypatch.setattr(FakeProvider, "setup", forbidden)
    monkeypatch.setattr(FakeProvider, "resolve", forbidden)
    monkeypatch.setattr(SecretReference, "resolve", forbidden)
    assert FakeProvider.collections[0].model_dump(mode="json") == {
        "name": "observatory:features",
        "unit_types": ["sensor", "road"],
        "crs": "EPSG:27700",
        "outputs": {},
    }
    metadata = resource_metadata(FakeProvider)
    assert metadata["secrets"] == ["token"]
    assert "label" in FakeProvider.config_model.model_fields
    forbidden.assert_not_called()
    assert (
        LocationCollection.model_validate_json(COLLECTION.model_dump_json())
        == COLLECTION
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "features"},
        {"name": "a:b:c"},
        {"name": "a: b"},
        {"unit_types": ()},
        {"unit_types": ("sensor", "sensor")},
        {"unit_types": ("",)},
        {"unit_types": ("two words",)},
        {"unit_types": (1,)},
        {"unit_types": ["sensor"]},
        {"crs": ""},
        {"crs": " "},
        {"extra": "value"},
    ],
)
def test_invalid_collection_metadata(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LocationCollection.model_validate(COLLECTION.model_dump() | changes)


@pytest.mark.parametrize(
    "collections", [(), (COLLECTION, COLLECTION), ("invalid",), [COLLECTION]]
)
def test_invalid_provider_declarations(collections: object) -> None:
    with pytest.raises(PluginDefinitionError):
        type("InvalidProvider", (LocationProvider,), {"collections": collections})


def test_provider_requires_resolution_implementation() -> None:
    abstract = type(
        "AbstractProvider",
        (LocationProvider,),
        {"collections": (COLLECTION,), "outputs": FakeProvider.outputs},
    )
    with pytest.raises(TypeError, match="abstract"):
        abstract(IOConfig(), {})


def test_batch_resolution_reuses_lifecycle_configuration_and_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    FakeProvider.events.clear()
    scope = _scope()
    with caplog.at_level(logging.INFO, logger="lyra.io"), scope.activate():
        provider = scope.get("locations")
        assert isinstance(provider, FakeProvider)
        for unit_type in COLLECTION.unit_types:
            result = provider.resolve(COLLECTION.name, unit_type, ["a/b", "001"])
            assert [feature.id for feature in result.features] == ["a/b", "001"]
            assert result.crs.properties.name == COLLECTION.crs
            assert result.features[0].properties == {"label": "station"}
            assert scope.get("locations") is provider
    assert FakeProvider.events == ["setup", "close"]
    assert not scope.instances
    events = [
        record.structured_fields
        for record in caplog.records
        if hasattr(record, "structured_fields")
        and isinstance(record.structured_fields, dict)
    ]
    assert len(events) == 2
    for event in events:
        assert event["resource"] == "locations"
        assert event["operation"] == "resolve"
        assert event["outcome"] == "success"
        assert event["job_id"] is None
        assert event["node"] is None
        assert event["duration_seconds"] >= 0


@pytest.mark.parametrize("ids", [["001", "missing"], ["missing", "wrong-type"]])
def test_partial_and_total_misses_preserve_scope_and_ids(
    ids: list[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    FakeProvider.events.clear()
    scope = _scope()
    with caplog.at_level(logging.INFO, logger="lyra.io"), scope.activate():
        provider = scope.get("locations")
        assert isinstance(provider, FakeProvider)
        with pytest.raises(UnresolvedLocationIDsError) as caught:
            provider.resolve(COLLECTION.name, "sensor", ids)
    assert caught.value.collection == COLLECTION.name
    assert caught.value.unit_type == "sensor"
    assert caught.value.ids == tuple(value for value in ids if value != "001")
    assert not isinstance(caught.value, LocationAcquisitionError)
    assert FakeProvider.events == ["setup", "close"]
    record = caplog.records[-1]
    assert hasattr(record, "structured_fields")
    event = record.structured_fields
    assert isinstance(event, dict)
    assert event["outcome"] == "failure"
    assert event["exception_class"] == "UnresolvedLocationIDsError"


def test_acquisition_failure_preserves_cause_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    FakeProvider.events.clear()
    cause = OSError("fake source unavailable")

    class BrokenProvider(FakeProvider):
        def setup(self) -> None:
            super().setup()
            message = "Cannot acquire locations"
            raise LocationAcquisitionError(message) from cause

    scope = _scope(BrokenProvider)
    with pytest.raises(LocationAcquisitionError) as caught, scope.activate():
        scope.get("locations")
    assert caught.value.__cause__ is cause
    assert not isinstance(caught.value, UnresolvedLocationIDsError)
    assert FakeProvider.events == ["setup", "close"]
    assert not scope.instances


def test_error_context_snapshots_ids() -> None:
    ids = ["001", "a/b"]
    unresolved = UnresolvedLocationIDsError(COLLECTION.name, "sensor", ids)
    acquisition = LocationAcquisitionError(
        "Source unavailable", collection=COLLECTION.name, unit_type="sensor", ids=ids
    )
    ids.clear()
    assert unresolved.ids == acquisition.ids == ("001", "a/b")
    assert acquisition.collection == COLLECTION.name
    assert acquisition.unit_type == "sensor"


@pytest.mark.parametrize(
    "outputs",
    [
        {},
        {("observatory:features", "sensor"): output_schema(label=str)},
        {("observatory:features", unit): object() for unit in ("sensor", "road")},
    ],
)
def test_provider_requires_schema_for_every_scope(outputs: dict) -> None:
    with pytest.raises(PluginDefinitionError, match="every declared scope"):
        type("InvalidSchemaProvider", (FakeProvider,), {"outputs": outputs})
