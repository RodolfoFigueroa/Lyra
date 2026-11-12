from copy import deepcopy

import pytest
from pydantic import ValidationError

from docs.scripts.generate_docs import create_openapi_app
from lyra.api.client.endpoints import create_job
from lyra.contracts.jobs import JobCreateRequest
from lyra.server.mcp.models import TOOL_CONTRACTS_BY_NAME


@pytest.mark.parametrize("section", ["inputs", "parameters"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "text",
        {"value": (1, 2)},
        {"value": {1, 2}},
        {1: "value"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
    ],
)
def test_sections_reject_invalid_json(section: str, value: object) -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate({"metric": "metric", section: value})


def test_request_owns_nested_json_and_schema_matches_transports() -> None:
    payload = {
        "metric": "metric",
        "inputs": {"places": {"ids": ["one"]}},
        "parameters": {"nested": [None, {"value": 2}]},
    }
    original = deepcopy(payload)
    request = JobCreateRequest.model_validate(payload)
    assert request.model_dump(include={"metric", "inputs", "parameters"}) == original
    nested = request.parameters["nested"]
    assert isinstance(nested, list)
    nested.append("new")
    places = request.inputs["places"]
    assert isinstance(places, dict)
    places["ids"] = []
    assert payload == original
    spec = create_job("metric", parameters={"nested": [None, {"value": 2}]})
    assert spec.json_body == {
        "metric": "metric",
        "inputs": {},
        "parameters": original["parameters"],
    }
    schema = JobCreateRequest.model_json_schema()
    assert TOOL_CONTRACTS_BY_NAME["lyra_run_metric"].input_schema == schema
    http_schema = create_openapi_app().openapi()["components"]["schemas"][
        "JobCreateRequest"
    ]
    assert http_schema["required"] == schema["required"] == ["metric"]
    assert http_schema["properties"].keys() == schema["properties"].keys()
    assert http_schema["additionalProperties"] is False


@pytest.mark.parametrize("metric", [None, 1, b"metric", object()])
def test_metric_requires_json_string(metric: object) -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate({"metric": metric})
