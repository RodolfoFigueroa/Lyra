"""Serialize MCP responses and enforce the inspection wire-size budget."""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent

from lyra.contracts.inspection import MAX_INSPECTION_BYTES, trim_inspection


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


def serialized_size(result: CallToolResult) -> int:
    """Return actual compact UTF-8 bytes including both payload representations."""
    return len(result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def serialize_result(
    payload: dict[str, Any], *, is_error: bool = False, bounded: bool = False
) -> CallToolResult:
    """Serialize one payload, trimming inspection details deterministically.

    Returns:
        Both MCP representations from the same schema-valid payload, or a small
        error when mandatory identifiers and access metadata cannot fit.
    """
    result = _wire_result(payload, is_error=is_error)
    if not bounded or serialized_size(result) <= MAX_INSPECTION_BYTES:
        return result
    if not is_error:
        while trim_inspection(payload):
            result = _wire_result(payload, is_error=False)
            if serialized_size(result) <= MAX_INSPECTION_BYTES:
                return result
    return _wire_result(
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
