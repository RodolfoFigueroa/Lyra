"""Persistence retains accepted values without a catalog."""

import json

import pytest
from pydantic import Field

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.plugins import LocationInputDeclaration
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from test_support.accepted_sources import routed_request
from test_support.canonical_routes import contract
from test_support.location_operations import Parameters as OperationParameters
from test_support.parameter_catalog import with_models
from test_support.plugin_graph import manifest
from tests.request_preparation_helpers import with_parameters


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

    catalog, request = routed_request()
    node = catalog.graph.nodes["score"].node
    if native:
        contract = node.location_inputs["places"].contract
        request["inputs"]["places"].update(provider=contract.family.collection)

    class Parameters(MetricParameters):
        values: list[int] = Field(default_factory=lambda: [1], description="Values")

    catalog = with_parameters(compile_catalog(catalog.manifests), Parameters)
    request["cache_revision"] = "run"
    plan = prepare_execution(catalog, request)
    schema = catalog.graph.nodes["score"].node.parameters_schema
    assert schema is not None
    schema.clear()
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.parameters == {"values": [1]}
    assert restored.request.model_dump(mode="json")["parameters"] == {"values": [1]}
    assert restored.cache_revision == "run"


def test_multihop() -> None:

    catalog, request = routed_request()
    operation = catalog.graph.nodes["select_0"].node
    assert operation.kind == "location_operation"
    target = operation.output_contract
    middle = contract("middle")
    middle.family = middle.family.model_copy(update={"unit_type": "middle"})
    assert operation.kind == "location_operation"
    operation.output_contract = middle
    second = operation.model_copy(deep=True)
    second.name = "second"
    second.location_inputs = {"source": LocationInputDeclaration(contract=middle)}
    assert second.kind == "location_operation"
    second.output_contract = target

    catalog = compile_catalog(
        {"test": manifest("test", catalog.graph.nodes["score"].node, operation, second)}
    )
    catalog = with_models(
        catalog, {"select_0": OperationParameters, "second": OperationParameters}
    )
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
