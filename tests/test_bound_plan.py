"""Persistence retains accepted values without a catalog."""

import json

import pytest
from pydantic import Field

from lyra.contracts.accepted import PreparedExecution
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from test_support.accepted_sources import routed_request


def test_round_trip() -> None:
    catalog, request = routed_request()
    plan = prepare_execution(catalog, request)
    payload = plan.model_dump_json()
    request["inputs"].clear()
    catalog.graph.nodes.clear()
    restored = PreparedExecution.model_validate_json(payload)
    assert restored == plan
    assert restored.parameters == {}
    assert restored.request.model_dump(mode="json")["inputs"]


@pytest.mark.parametrize("field", ["node", "inputs", "revisions", "request", "outputs"])
def test_malformed(field: str) -> None:
    catalog, request = routed_request()
    payload = json.loads(prepare_execution(catalog, request).model_dump_json())
    payload[field] = None
    with pytest.raises(ValueError, match="validation error"):
        PreparedExecution.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("native", [False, True])
def test_defaults_and_identity(*, native: bool) -> None:

    class Parameters(MetricParameters):
        values: list[int] = Field(default_factory=lambda: [1], description="Values")

    catalog, request = routed_request(Parameters)
    node = catalog.graph.nodes["score"].node
    if native:
        contract = node.location_inputs["places"].contract
        request["inputs"]["places"].update(provider=contract.family.collection)

    plan = prepare_execution(catalog, request)
    schema = catalog.graph.nodes["score"].node.parameters_schema
    assert schema is not None
    schema.clear()
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.parameters == {"values": [1]}
    assert restored.request.model_dump(mode="json")["parameters"] == {"values": [1]}


def test_multihop() -> None:

    catalog, request = routed_request(multihop=True)
    plan = prepare_execution(catalog, request)
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.inputs[0].binding.operations == ("select_0", "second")


@pytest.mark.parametrize("change", ["identity", "revision", "input"])
def test_inconsistent_binding_metadata(change: str) -> None:
    catalog, request = routed_request()
    payload = prepare_execution(catalog, request).model_dump(mode="json")
    if change == "identity":
        payload["node"] = "different"
    elif change == "revision":
        payload["revisions"] = {"calculation": [], "selection": []}
    else:
        payload["request"]["inputs"].pop("reference")
    with pytest.raises(ValueError, match=r"binding|revision"):
        PreparedExecution.model_validate_json(json.dumps(payload))
