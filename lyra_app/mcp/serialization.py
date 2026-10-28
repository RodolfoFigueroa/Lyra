"""Serialize MCP responses and enforce the inspection wire-size budget."""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent

MAX_INSPECTION_BYTES = 64 * 1024


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
        while _trim_inspection(payload):
            result = _wire_result(payload, is_error=False)
            if serialized_size(result) <= MAX_INSPECTION_BYTES:
                return result
    return _wire_result(
        {
            "error": {
                "code": "result_response_too_large",
                "message": (
                    "The mandatory result response exceeds the inspection size limit. "
                    "Retrieve the complete REST descriptor."
                ),
            }
        },
        is_error=True,
    )


def _trim_inspection(payload: dict[str, Any]) -> bool:
    truncation = payload["truncation"]
    omitted = truncation["omitted_sections"]
    for output in reversed(list(payload.get("outputs", {}).values())):
        if output["preview"] is not None:
            preview = output["preview"]
            rows = preview if isinstance(preview, list) else preview["features"]
            output["truncation"]["omitted_rows"] += len(rows)
            output["truncation"]["omitted_sections"].append("preview")
            output["preview"] = None
            return True
        if output["metadata"] is not None:
            output["metadata"] = None
            output["truncation"]["omitted_sections"].append("metadata")
            return True
    provenance = payload.get("provenance")
    if provenance and provenance.get("input") is not None:
        provenance["input"] = None
        omitted.append("provenance.input")
        return True
    if payload.get("error") is not None:
        payload["error"] = None
        omitted.append("error")
        return True
    return False
