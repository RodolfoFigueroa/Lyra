"""Catalog-only acceptance captures routes and rejects malformed requests."""

from collections.abc import Iterator
from unittest.mock import Mock, create_autospec

import pytest
from pydantic import Field

from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.parameters import MetricParameters
from test_support.accepted_sources import routed_request
from test_support.location_operations import Places


def test_capture() -> None:
    catalog, request = routed_request()
    prepared = prepare_execution(catalog, request)
    assert prepared.inputs[0].binding.operations == ("select_0",)
    assert prepared.inputs[1].binding.operations == ()
    assert all(binding.revision is None for binding in prepared.revisions.bindings)
    request["inputs"]["places"]["ids"].append("changed")
    assert prepared.inputs[0].ids == ("unknown",)
    assert prepared.parameters == {}


def test_empty_interventions_preserve_ordinary_preparation() -> None:
    catalog, request = routed_request()
    ordinary = prepare_execution(catalog, request)
    request["interventions"] = []
    assert prepare_execution(catalog, request) == ordinary


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "blank_id",
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
    elif change == "blank_id":
        request["inputs"]["places"]["ids"] = [""]
    elif change == "source":
        request["inputs"]["places"]["provider"] = "missing"
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
    class Parameters(MetricParameters):
        weight: int = Field(2, ge=1, description="Weight")

    catalog, request = routed_request(Parameters)
    prepared = prepare_execution(catalog, request)
    assert prepared.parameters == {"weight": 2}
    prepared.parameters["weight"] = 10
    assert prepare_execution(catalog, request).parameters == {"weight": 2}
    request["parameters"] = {"weight": 0}
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)
    catalog, request = routed_request(Parameters, private=True)
    with pytest.raises(PreparationError):
        prepare_execution(catalog, request)


pytestmark = pytest.mark.usefixtures("no_side_effects")


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every acceptance and rejection leaves acquisition and enqueue untouched."""
    acquisition = create_autospec(
        Places.resolve, side_effect=AssertionError("Unexpected acquisition")
    )
    enqueue = Mock(side_effect=AssertionError("Unexpected enqueue"))
    monkeypatch.setattr(Places, "resolve", acquisition)
    monkeypatch.setattr("lyra.server.job_store.JobStore.enqueue", enqueue)
    yield
    acquisition.assert_not_called()
    enqueue.assert_not_called()


def test_native_and_owned_nested_defaults() -> None:
    class Options(MetricParameters):
        values: list[int] = Field(default_factory=lambda: [1], description="Values")

    class Parameters(MetricParameters):
        options: Options = Field(default_factory=Options, description="Options")

    catalog, request = routed_request(Parameters)
    node = catalog.graph.nodes["score"].node
    native = node.location_inputs["places"].contract
    request["inputs"]["places"] = {
        "provider": native.family.collection,
        "ids": ["unknown", "unknown"],
    }

    prepared = prepare_execution(catalog, request)
    assert prepared.inputs[0].binding.operations == ()
    assert prepared.inputs[0].ids == ("unknown", "unknown")
    assert prepared.computation_identity == catalog.computation.plan(
        prepared.node,
        prepared.inputs,
        prepared.dependency_routes,
        prepared.parameter_uses,
    )
    assert prepared.parameters == {"options": {"values": [1]}}
    native.data_revision = "changed"
    schema = catalog.graph.nodes["score"].node.parameters_schema
    assert schema is not None
    schema.clear()
    values = prepared.request.parameters
    options = values["options"]
    assert isinstance(options, dict)
    nested = options["values"]
    assert isinstance(nested, list)
    nested.append(2)
    assert prepared.parameters == {"options": {"values": [1]}}
    assert all(binding.revision is None for binding in prepared.revisions.bindings)
