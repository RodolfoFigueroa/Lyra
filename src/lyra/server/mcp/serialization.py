"""Serialize MCP responses and enforce the inspection wire-size budget."""

import json
from typing import Any

from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, JSONRPCResponse, RequestId, TextContent
from pydantic import BaseModel

from lyra.contracts.errors import ResultAccessError
from lyra.contracts.inspection import MAX_INSPECTION_BYTES, trim_inspection
from lyra.contracts.retrieval_errors import error_payload
from lyra.server.response_budget import MAX_RETRIEVAL_BYTES, ResponseBudget


def mcp_response_size(result: CallToolResult, request_id: RequestId) -> int:
    """Return SDK JSON response bytes including the actual JSON-RPC request ID."""
    response = JSONRPCResponse(
        jsonrpc="2.0",
        id=request_id,
        result=result.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return len(
        response.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    )


def mcp_response_budget(
    metadata: FuncMetadata,
    request_id: RequestId,
    *,
    limit: int = MAX_RETRIEVAL_BYTES,
) -> ResponseBudget[BaseModel]:
    """Size typed candidates through the registered tool's SDK output converter.

    The SDK owns aliases, union wrapping, and text/structured representations.
    Handlers return the chosen model normally so dispatch and schema validation
    remain SDK-owned. Supply the active protocol request ID, never estimated
    envelope overhead.

    Returns:
        A budget for complete/preview candidates and download links.
    """

    def size(candidate: BaseModel) -> int:
        content, structured = metadata.convert_result(candidate)
        result = CallToolResult(content=list(content), structuredContent=structured)
        return mcp_response_size(result, request_id)

    return ResponseBudget(size, limit)


def retrieval_error_result(
    error: ResultAccessError, request_id: RequestId
) -> CallToolResult:
    """Return bounded domain details, distinct from SDK argument-validation errors."""
    result = _wire_result(
        {"error": error_payload(error).model_dump(mode="json")}, is_error=True
    )
    return ResponseBudget[CallToolResult](
        lambda candidate: mcp_response_size(candidate, request_id)
    ).require_fits(result)


def _wire_result(payload: dict[str, Any], *, is_error: bool) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structuredContent=payload,
        isError=is_error,
    )


def serialize_result(
    payload: dict[str, Any], request_id: RequestId, *, is_error: bool = False
) -> CallToolResult:
    """Serialize one payload, trimming inspection details deterministically.

    Returns:
        Both MCP representations from the same schema-valid payload, or a small
        error when mandatory identifiers and access metadata cannot fit.
    """
    result = _wire_result(payload, is_error=is_error)
    budget = ResponseBudget[CallToolResult](
        lambda candidate: mcp_response_size(candidate, request_id), MAX_INSPECTION_BYTES
    )
    if budget.fits(result):
        return result
    if not is_error:
        while trim_inspection(payload.get("result", payload)):
            result = _wire_result(payload, is_error=False)
            if budget.fits(result):
                return result
    fallback = _wire_result(
        {
            "error": {
                "code": "result_response_too_large",
                "message": (
                    "The mandatory result response exceeds the inspection size limit. "
                    "Retrieve the complete result."
                ),
            }
        },
        is_error=True,
    )
    return budget.require_fits(fallback)
