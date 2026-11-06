"""Catalog-only acceptance captures routes and rejects malformed requests."""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from test_support.accepted_sources import routed_request


def test_capture() -> None:
    catalog, request = routed_request()
    prepared = prepare_execution(catalog, request)
    assert prepared.inputs[0].binding.operations == ("select_0",)
    assert prepared.inputs[1].binding.operations == ()
    assert all(binding.revision is None for binding in prepared.revisions.bindings)
    request["inputs"]["places"]["ids"].append("changed")
    assert catalog.graph.nodes["score"].node.kind == "metric"
    catalog.graph.nodes["score"].node.private = True
    assert prepared.inputs[0].ids == ("unknown",)
    assert prepared.parameters == {}


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "empty",
        "source",
        "input",
        "parameter",
        "private",
        "edition",
        "operation",
    ],
)
def test_rejections(change: str) -> None:
    catalog, request = routed_request()
    if change == "extra":
        request["pipeline"] = []
    elif change == "empty":
        request["inputs"]["places"]["ids"] = []
    elif change == "source":
        request["inputs"]["places"]["unit_type"] = "missing"
    elif change == "input":
        request["inputs"].pop("places")
    elif change == "parameter":
        request["parameters"] = {"unknown": 1}
    elif change == "private":
        request["metric"] = "missing"
    elif change == "edition":
        request["edition"] = "missing"
    else:
        request["metric"] = "select_0"
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)


def test_named_edition_defaults_and_private() -> None:
    catalog, request = routed_request()
    node = catalog.graph.nodes["score"].node
    assert node.kind == "metric"
    node.family = "family"
    assert node.kind == "metric"
    node.edition = "only"
    node.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {"weight": {"type": "integer", "minimum": 1, "default": 2}},
    }
    catalog = compile_catalog(catalog.manifests)
    request["metric"] = "family"
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)
    request["edition"] = "only"
    prepared = prepare_execution(catalog, request)
    assert prepared.parameters == {"weight": 2}
    prepared.parameters["weight"] = 10
    assert prepared.parameters == {"weight": 2}
    request["parameters"] = {"weight": 0}
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)
    assert catalog.graph.nodes["score"].node.kind == "metric"
    catalog.graph.nodes["score"].node.private = True
    catalog = compile_catalog(catalog.manifests)
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)


pytestmark = pytest.mark.usefixtures("no_side_effects")


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every acceptance and rejection leaves acquisition and enqueue untouched."""
    acquisition = Mock(side_effect=AssertionError("Unexpected acquisition"))
    enqueue = Mock(side_effect=AssertionError("Unexpected enqueue"))
    monkeypatch.setattr("lyra.sdk.locations.LocationProvider.resolve", acquisition)
    monkeypatch.setattr("lyra.server.job_store.JobStore.enqueue", enqueue)
    yield
    acquisition.assert_not_called()
    enqueue.assert_not_called()


def test_native_and_owned_nested_defaults() -> None:
    catalog, request = routed_request()
    node = catalog.graph.nodes["score"].node
    native = node.location_inputs["places"].contract
    request["inputs"]["places"] = {
        "collection": native.family.collection,
        "unit_type": native.family.unit_type,
        "ids": ["unknown", "unknown"],
    }
    node.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "options": {
                "type": "object",
                "default": {},
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "default": [1],
                    }
                },
            }
        },
    }
    request["cache_revision"] = "run"
    prepared = prepare_execution(catalog, request)
    assert prepared.inputs[0].binding.operations == ()
    assert prepared.inputs[0].ids == ("unknown", "unknown")
    assert prepared.cache_revision == "run"
    assert prepared.computation_identity == catalog.computation.plan(
        prepared.node, prepared.inputs
    )
    assert prepared.parameters == {"options": {"values": [1]}}
    native.data_revision = "changed"
    node.parameters_schema.clear()
    values = prepared.parameters
    options = values["options"]
    assert isinstance(options, dict)
    nested = options["values"]
    assert isinstance(nested, list)
    nested.append(2)
    assert prepared.parameters == {"options": {"values": [1]}}
    assert all(binding.revision is None for binding in prepared.revisions.bindings)
