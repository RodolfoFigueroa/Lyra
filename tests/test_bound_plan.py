"""Persistence retains accepted values without a catalog."""

import json

import pytest
from lyra.sdk.bound_plan import deserialize_bound_plan, serialize_bound_plan
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.models.plugin import LocationInputDeclaration
from lyra.sdk.preparation import prepare_execution

from tests.test_canonical_routes import contract
from tests.test_plugin_graph import manifest
from tests.test_request_preparation import fixture


def test_round_trip() -> None:
    catalog, request = fixture()
    plan = prepare_execution(catalog, request)
    payload = serialize_bound_plan(plan)
    request["inputs"].clear()
    catalog.graph.nodes.clear()
    restored = deserialize_bound_plan(payload)
    assert restored == plan
    assert restored.parameters == {}
    assert restored.request.model_dump(mode="json")["inputs"]


@pytest.mark.parametrize(
    "field", ["node", "inputs", "revisions", "request", "contracts"]
)
def test_malformed(field: str) -> None:
    catalog, request = fixture()
    payload = json.loads(serialize_bound_plan(prepare_execution(catalog, request)))
    payload[field] = None
    with pytest.raises(ValueError, match="validation error"):
        deserialize_bound_plan(json.dumps(payload))


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_defaults_and_identity(*, named: bool, native: bool) -> None:

    catalog, request = fixture()
    node = catalog.graph.nodes["score"].node
    if named:
        assert node.kind == "metric"
        node.family = "family"
        assert node.kind == "metric"
        node.edition = "chosen"
        request.update(metric="family", edition="chosen")
    if native:
        contract = node.location_inputs["places"].contract
        request["inputs"]["places"].update(
            collection=contract.family.collection, unit_type=contract.family.unit_type
        )
    node.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"values": {"type": "array", "default": [1]}},
    }
    catalog = compile_catalog(catalog.manifests)
    request["cache_revision"] = "run"
    plan = prepare_execution(catalog, request)
    node.parameters_schema.clear()
    restored = deserialize_bound_plan(serialize_bound_plan(plan))
    assert restored == plan
    assert restored.parameters == {"values": [1]}
    assert restored.request.model_dump(mode="json")["parameters"] == {}
    assert restored.cache_revision == "run"


def test_multihop() -> None:

    catalog, request = fixture()
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
    plan = prepare_execution(catalog, request)
    restored = deserialize_bound_plan(serialize_bound_plan(plan))
    assert restored == plan
    assert restored.inputs[0].binding.operations == ("select_0", "second")


@pytest.mark.parametrize("change", ["identity", "revision", "input"])
def test_inconsistent_binding_metadata(change: str) -> None:
    catalog, request = fixture()
    payload = json.loads(serialize_bound_plan(prepare_execution(catalog, request)))
    metadata = payload["contracts"]
    if change == "identity":
        metadata["nodes"]["score"]["family"] = "different"
    elif change == "revision":
        metadata["nodes"]["score"]["location_inputs"]["places"]["contract"][
            "data_revision"
        ] = "different"
    else:
        payload["inputs"] = payload["inputs"][:1]
        original = payload["request"]
        original["inputs"].pop("reference")
        payload["request"] = original
    payload["contracts"] = metadata
    with pytest.raises(ValueError, match=r"binding|revision"):
        deserialize_bound_plan(json.dumps(payload))
