import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from docs.scripts.generate_docs import create_openapi_app
from docs.scripts.mcp_tools import advertised_tools
from lyra.api.client.endpoints import create_job
from lyra.contracts.jobs import JobCreateRequest
from test_support.mcp import call_tool
from tests.job_id_helpers import TEST_JOB_ID


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
    mcp_schema = advertised_tools()["lyra_run_metric"].inputSchema
    assert mcp_schema["properties"] == schema["properties"]
    assert mcp_schema["required"] == schema["required"]
    assert mcp_schema["additionalProperties"] is False
    assert mcp_schema["$defs"] == schema["$defs"]
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


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("lyra_list_metrics", {}),
        ("lyra_search_metrics", {"query": "report"}),
        ("lyra_get_metric", {"metric": "report"}),
        ("lyra_get_metric_dependencies", {"metric": "report"}),
        ("lyra_run_metric", {"metric": "report"}),
        ("lyra_get_job_result", {"job_id": TEST_JOB_ID}),
        ("lyra_download_result", {"job_id": TEST_JOB_ID}),
    ],
)
def test_unknown_fields_are_rejected_by_schema_and_call(
    name: str, arguments: dict
) -> None:
    schema = advertised_tools()[name].inputSchema
    validator = Draft202012Validator(schema)
    assert validator.is_valid(arguments)
    arguments = {**arguments, "unexpected": True}
    assert not validator.is_valid(arguments)
    backend = Mock()
    result = asyncio.run(call_tool(backend, name, arguments))
    assert result["isError"] is True
    assert backend.mock_calls == []


@pytest.mark.parametrize("name", ["lyra_get_metric", "lyra_get_metric_dependencies"])
def test_unknown_selection_field_is_rejected_by_schema_and_call(name: str) -> None:
    schema = advertised_tools()[name].inputSchema
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({"metric": "report", "edition": "release"})
    assert not validator.is_valid({"metric": "report", "edition": None})
    arguments = {"metric": "report", "edition": ""}
    assert not validator.is_valid(arguments)
    backend = Mock()
    result = asyncio.run(call_tool(backend, name, arguments))
    assert result["isError"] is True
    assert backend.mock_calls == []
