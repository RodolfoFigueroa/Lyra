import logging
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, create_autospec

import pytest

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    PluginDefinitionError,
    UnresolvedLocationIDsError,
)
from lyra.contracts.resources import IOConfig, ResourceConfig, SecretReference
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.provider_acquisition import acquire_provider_input
from lyra.runtime.resources import ResourceScope
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.resources import resource_metadata
from tests.provider_helpers import FakeProvider

if TYPE_CHECKING:
    from collections.abc import Callable


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


def _selection(ids: list[str]) -> PreparedProviderInput:
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources={"locations": FakeProvider})}
    )
    return PreparedProviderInput("places", frozenset(ids), registry[FakeProvider.id])


def test_metadata_inspection_never_acquires_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("Inspection acquired a resource"))
    for name in ("__init__", "setup", "resolve"):
        monkeypatch.setattr(
            FakeProvider,
            name,
            create_autospec(FakeProvider.resolve, side_effect=forbidden)
            if name == "resolve"
            else forbidden,
        )
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    registered = _selection([]).provider
    assert registered.scope.provider_id == FakeProvider.id
    assert {column.name for column in registered.schema.metadata.columns} == {
        "location_id",
        "geometry",
        "label",
    }
    assert resource_metadata(FakeProvider)["secrets"] == ["token"]
    assert "label" in FakeProvider.config_model.model_fields
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [{"id": ""}, {"id": 1}, {"schema": None}, {"origins": None}, {"code_revision": 1}],
)
def test_invalid_provider_declarations(changes: dict[str, object]) -> None:
    provider = type("Invalid", (FakeProvider,), changes)
    with pytest.raises(PluginDefinitionError):
        compile_provider_registry(
            {"test": PluginDefinition(resources={"locations": provider})}
        )


def test_provider_requires_resolution_implementation() -> None:
    abstract = type(
        "AbstractProvider",
        (LocationProvider,),
        {"id": "abstract", "schema": FakeProvider.schema},
    )
    with pytest.raises(TypeError, match="abstract"):
        cast("Callable[..., LocationProvider]", abstract)(IOConfig(), {})


def test_batch_resolution_reuses_lifecycle_configuration_and_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    FakeProvider.events.clear()
    scope = _scope()
    selected = _selection(["a/b", "001"])
    with caplog.at_level(logging.INFO, logger="lyra.io"), scope.activate():
        for _ in range(2):
            result = acquire_provider_input(selected, scope, needed_ids=["a/b", "001"])
            assert result.frame.location_id.tolist() == ["a/b", "001"]
            assert str(result.frame.crs) == "EPSG:27700"
            assert result.frame.label.tolist() == ["station", "station"]
            assert scope.get("locations") is scope.get("locations")
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
    ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCATION_TEST_TOKEN", "fake-token")
    FakeProvider.events.clear()
    scope = _scope()
    with pytest.raises(LocationProviderContractError) as caught, scope.activate():
        acquire_provider_input(_selection(ids), scope)
    assert set(caught.value.ids) == set(ids) - {"001"}
    assert FakeProvider.events == ["setup", "close"]
    assert not scope.instances


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
    assert FakeProvider.events == ["setup", "close"]
    assert not scope.instances


def test_error_context_snapshots_ids() -> None:
    ids = ["001", "a/b"]
    unresolved = UnresolvedLocationIDsError("test:features", "sensor", ids)
    acquisition = LocationAcquisitionError(
        "Source unavailable", collection="test:features", unit_type="sensor", ids=ids
    )
    ids.clear()
    assert unresolved.ids == acquisition.ids == ("001", "a/b")
    assert acquisition.collection == "test:features"
    assert acquisition.unit_type == "sensor"
