import asyncio
import json
from dataclasses import replace
from typing import Literal

import httpx
import pytest
from jsonschema import validate
from mcp.types import CallToolResult
from pydantic import ConfigDict, Field, ValidationError

from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ReportIntegrityError,
    ReportNotFoundError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.retrieval_errors import decode_error, error_payload, error_status
from lyra.contracts.retrieval_paths import (
    absolute_api_url,
    report_download_path,
    report_inspection_path,
    result_description_path,
    table_download_path,
)
from lyra.contracts.strict import StrictBaseModel
from lyra.server.mcp.serialization import mcp_response_size, retrieval_error_result
from lyra.server.mcp.server import LyraFastMCP
from lyra.server.response_budget import (
    MAX_RETRIEVAL_BYTES,
    ResponseTooLargeError,
    http_json_response,
    http_response_budget,
    http_response_size,
)
from lyra.server.routes.retrieval_errors import retrieval_error_response

_REFERENCE = "a" * 32
_EXECUTION = "b" * 32
_ERRORS = [
    LocationInputUnavailableError(_REFERENCE, "reference_blocks"),
    ResultNotFoundError(_REFERENCE, input_name="blocks"),
    ResultIntegrityError(_REFERENCE, input_name="blocks"),
    ResultNotFoundError(_REFERENCE),
    ResultNotFoundError(_REFERENCE, '雪/"?'),
    ResultIntegrityError(_REFERENCE),
    ResultIntegrityError(_REFERENCE, "a"),
    ReportNotFoundError(_EXECUTION),
    ReportIntegrityError(_EXECUTION),
]


@pytest.mark.parametrize("error", _ERRORS)
def test_error_payload_round_trip(error: ResultAccessError) -> None:
    payload = error_payload(error)
    http_response = retrieval_error_response(error)
    assert http_response.status_code == error_status(payload)
    expected_statuses: dict[type[ResultAccessError], int] = {
        ResultNotFoundError: 404,
        ReportNotFoundError: 404,
        ResultIntegrityError: 500,
        ReportIntegrityError: 500,
        LocationInputUnavailableError: 409,
    }
    assert http_response.status_code == expected_statuses[type(error)]
    assert json.loads(bytes(http_response.body))["detail"] == payload.model_dump()
    restored = decode_error(payload.model_dump(), status=http_response.status_code)
    assert type(restored) is type(error)
    assert vars(restored) == vars(error)
    changed_message = {**payload.model_dump(), "message": "Informational text changed"}
    restored = decode_error(changed_message, status=http_response.status_code)
    assert type(restored) is type(error)
    assert vars(restored) == vars(error)
    # Exception diagnostics and causes are not part of the public payload.
    error.args = ("private database connection detail",)
    assert error_payload(error) == payload
    assert len(http_response.body) <= MAX_RETRIEVAL_BYTES


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"code": "result_not_found", "message": "absent"}, 404),
        ({"code": "unknown", "message": "ResultNotFoundError"}, 404),
        ({"message": "ResultNotFoundError"}, 404),
        ({"code": "result_not_found", "message": "absent", "job_id": "x"}, 404),
        ({"code": "report_not_found", "message": "absent", "job_id": "x"}, 401),
        ({"code": "report_not_found", "message": 2, "job_id": "x"}, 404),
        (None, 500),
    ],
)
def test_unrecognized_errors_are_not_guessed(payload: object, status: int) -> None:
    assert decode_error(payload, status=status) is None


def test_paths_keep_resource_kinds_and_encoded_segments() -> None:
    result = f"results/{_REFERENCE}"
    report = f"executions/{_EXECUTION}/report"
    assert result_description_path(_REFERENCE) == result
    assert report_inspection_path(_EXECUTION) == f"{report}/inspection"
    assert report_download_path(_EXECUTION) == f"{report}/download"
    table = f"{result}/tables/download?output_name=%E9%9B%AA+%2F%25%3F%23"
    assert table_download_path(_REFERENCE, "雪 /%?#") == table
    assert (
        absolute_api_url("https://example.test:8443/public/api/", table)
        == f"https://example.test:8443/public/api/{table}"
    )
    assert absolute_api_url("https://example.test/public/api/", f"/{result}") == (
        f"https://example.test/public/api/{result}"
    )
    with pytest.raises(ValidationError):
        table_download_path("bad", "snow")
    with pytest.raises(ValidationError):
        report_download_path("bad")


class Complete(StrictBaseModel):
    model_config = ConfigDict(validate_by_name=True)

    kind: Literal["complete"] = "complete"
    body: str = Field(alias="description")


class Preview(StrictBaseModel):
    kind: Literal["preview"] = "preview"
    omitted: list[str] = Field(default_factory=lambda: ["description"])
    download_url: str = "https://example.test/api/download"


def test_http_whole_candidate_selection_matches_encoded_response() -> None:
    complete = Complete(body='雪 "\\\n' * 200)
    preview = Preview()
    original = complete.model_copy(deep=True)
    size = http_response_size(complete)
    response = http_json_response(complete)
    assert size == len(response.body)
    assert json.loads(bytes(response.body))["description"] == complete.body
    assert size > len(bytes(response.body).decode())
    assert http_response_budget(limit=size).choose(complete, preview) is complete
    assert http_response_budget(limit=size - 1).choose(complete, preview) is preview
    assert complete == original
    with pytest.raises(ResponseTooLargeError):
        http_response_budget(limit=http_response_size(preview) - 1).choose(
            complete, preview
        )
    with pytest.raises(ResponseTooLargeError):
        http_response_budget(limit=size - 1).require_fits(complete)


def test_fixed_error_fields_are_not_truncated_to_fit() -> None:
    error = ResultNotFoundError(_REFERENCE, "雪" * MAX_RETRIEVAL_BYTES)
    with pytest.raises(ResponseTooLargeError):
        retrieval_error_response(error)
    with pytest.raises(ResponseTooLargeError):
        retrieval_error_result(error, 1)
    assert error.output_name == "雪" * MAX_RETRIEVAL_BYTES


@pytest.mark.parametrize("request_id", [7, '雪"\\\n' * 40])
def test_sdk_union_response_size_and_boundary_selection(request_id: int | str) -> None:
    server = LyraFastMCP(
        json_response=True, stateless_http=True, streamable_http_path="/"
    )
    measured: list[int] = []
    body = '雪 "\\\n' * 200

    @server.tool()
    def inspect(value: str, limit: int = MAX_RETRIEVAL_BYTES) -> Complete | Preview:
        budget = replace(server.retrieval_budget("inspect"), limit=limit)
        selected = budget.choose(Complete(body=value), Preview())
        measured.append(budget.size(selected))
        assert isinstance(selected, Complete | Preview)
        return selected

    @server.tool()
    def link() -> Preview:
        preview = Preview()
        budget = server.retrieval_budget("link")
        budget.require_fits(preview)
        measured.append(budget.size(preview))
        return preview

    async def run() -> None:
        app = server.streamable_http_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client,
        ):

            async def call(limit: int, value: str = body) -> httpx.Response:
                response = await client.post(
                    "/",
                    headers={"Accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "inspect",
                            "arguments": {"value": value, "limit": limit},
                        },
                    },
                )
                assert response.status_code == 200
                assert len(response.content) == measured[-1]
                result = response.json()["result"]
                assert not result["isError"]
                definitions = await server.list_tools()
                validate(result["structuredContent"], definitions[0].outputSchema)
                assert mcp_response_size(
                    CallToolResult.model_validate(result), request_id
                ) == len(response.content)
                return response

            complete = await call(MAX_RETRIEVAL_BYTES)
            assert complete.json()["result"]["structuredContent"]["result"] == {
                "kind": "complete",
                "description": body,
            }
            exact = await call(len(complete.content))
            assert exact.content == complete.content
            preview = await call(len(complete.content) - 1)
            assert (
                preview.json()["result"]["structuredContent"]["result"]
                == Preview().model_dump()
            )
            assert len(preview.content) < len(complete.content)
            large = await call(MAX_RETRIEVAL_BYTES, body * 30)
            assert large.content == preview.content
            direct = await client.post(
                "/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": "link", "arguments": {}},
                },
            )
            assert direct.status_code == 200
            assert len(direct.content) == measured[-1]
            assert (
                direct.json()["result"]["structuredContent"] == Preview().model_dump()
            )

    asyncio.run(run())


@pytest.mark.parametrize("error", _ERRORS)
def test_sdk_domain_errors_preserve_context_and_validation_is_distinct(
    error: ResultAccessError,
) -> None:
    server = LyraFastMCP(
        json_response=True, stateless_http=True, streamable_http_path="/"
    )

    @server.tool()
    def lookup(index: int) -> Complete:
        del index
        raise error

    async def run() -> None:
        app = server.streamable_http_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client,
        ):
            for arguments in ({"index": 1}, {"index": "invalid"}):
                response = await client.post(
                    "/",
                    headers={"Accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "request-雪",
                        "method": "tools/call",
                        "params": {"name": "lookup", "arguments": arguments},
                    },
                )
                assert response.status_code == 200
                result = response.json()["result"]
                assert result["isError"]
                if arguments["index"] == 1:
                    payload = error_payload(error)
                    assert result["structuredContent"] == {
                        "error": payload.model_dump()
                    }
                    assert (
                        json.loads(result["content"][0]["text"])
                        == result["structuredContent"]
                    )
                    assert len(response.content) == mcp_response_size(
                        retrieval_error_result(error, "request-雪"), "request-雪"
                    )
                else:
                    assert "structuredContent" not in result
                    assert "validation error" in result["content"][0]["text"]

    asyncio.run(run())
