"""MCP tool implementations for querying and operating Lyra."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

from redis.exceptions import RedisError
from typing_extensions import override

from lyra.contracts.metrics import normalize_metric_search_tokens
from lyra.contracts.types import JsonObject, JsonValue, validate_json_value
from lyra.server.job_submission import (
    MetricPayloadValidationError,
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra.server.mcp.dependencies import inspect_dependencies
from lyra.server.mcp.models import (
    TOOL_CONTRACTS_BY_NAME,
    DownloadResultOutput,
    RunMetricOutput,
)
from lyra.server.mcp.results import handoff
from lyra.server.result_projection import project_observation
from lyra.server.results import ResultUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from lyra.contracts.jobs import (
        JobCreateRequest,
        JobCreateResponse,
    )
    from lyra.runtime.planning.discovery import Discovery
    from lyra.server.job_store import JobObservation
    from lyra.server.mcp.models import (
        GetJobResultInput,
        GetMetricInput,
        ListMetricsInput,
        ResultRefInput,
        SearchMetricsInput,
    )
    from lyra.server.runtime import ApplicationRuntime

OPERATION_TIMEOUT_SECONDS = 30.0
_RESULT_REF_PATTERN = re.compile(r"^lyra://results/([^/?#\s]+)$")
_UNKNOWN_METRIC_ERROR = "unknown_metric"
_INVALID_PARAMETERS_ERROR = "invalid_parameters"
_BACKEND_ERROR = "backend_error"
_METRIC_CURSOR_VERSION = 1
_MAX_COMPACT_DESCRIPTION_LENGTH = 240


class LyraMCPBackend(Protocol):
    """Define the domain operations used by transport-independent MCP tools."""

    async def get_discovery(self) -> Discovery:
        """Return the captured public family projections."""
        ...

    async def create_job(
        self,
        request: JobCreateRequest,
    ) -> JobCreateResponse:
        """Validate and submit a metric job through the Lyra domain service."""
        ...

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Read native lifecycle state and best-effort retained metadata."""
        ...


@dataclass
class ToolCallError(Exception):
    """Transport-independent domain failure returned as a structured tool error."""

    code: str
    message: str
    details: JsonValue = None

    def to_payload(self) -> dict[str, Any]:
        """Render this failure using the MCP tool error envelope.

        Returns:
            A structured error object with optional domain-specific details.
        """
        error: JsonObject = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


class InProcessLyraBackend(LyraMCPBackend):
    """Implement MCP domain operations directly against this Lyra process."""

    def __init__(self, runtime: Callable[[], ApplicationRuntime]) -> None:
        """Bind the backend to its owning application runtime accessor."""
        self.runtime = runtime

    @override
    async def get_discovery(self) -> Discovery:
        return self.runtime().catalog.discovery

    @override
    async def create_job(
        self,
        request: JobCreateRequest,
    ) -> JobCreateResponse:
        runtime = self.runtime()
        try:
            return await submit_job(
                request,
                catalog=runtime.catalog,
                jobs=runtime.jobs,
            )
        except UnknownMetricError as exc:
            raise ToolCallError(_UNKNOWN_METRIC_ERROR, str(exc)) from exc
        except MetricPayloadValidationError as exc:
            raise ToolCallError(
                _INVALID_PARAMETERS_ERROR,
                "Invalid metric parameters.",
                validate_json_value(exc.errors),
            ) from exc
        except SubmissionUnavailableError as exc:
            raise ToolCallError(_BACKEND_ERROR, str(exc), {"retryable": True}) from exc

    @override
    async def observe_job(self, job_id: str) -> JobObservation | None:
        try:
            return await self.runtime().results.observe_job(job_id)
        except ResultUnavailableError as exc:
            code = "result_unavailable"
            raise ToolCallError(code, "The result artifact is unavailable.") from exc
        except (ValueError, TypeError) as exc:
            code = "result_observation_error"
            message = "The retained job state could not be decoded."
            raise ToolCallError(code, message) from exc


async def execute_tool(
    name: str,
    arguments: BaseModel,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> BaseModel:
    """Execute once under a fixed job-operation deadline and validate the output.

    Returns:
        A typed output ready for serialization at the transport boundary.

    Raises:
        ToolCallError: For domain, timeout, or infrastructure failures.
    """
    try:
        timeout = (
            OPERATION_TIMEOUT_SECONDS
            if name
            in {"lyra_run_metric", "lyra_get_job_result", "lyra_download_result"}
            else None
        )
        async with asyncio.timeout(timeout):
            payload = await _execute_tool(
                name, arguments, backend, public_api_base_url=public_api_base_url
            )
        return TOOL_CONTRACTS_BY_NAME[name].output_adapter.validate_python(payload)
    except TimeoutError as exc:
        action = (
            "Retry observation."
            if name != "lyra_run_metric"
            else ("Submission may have succeeded; another submission may duplicate it.")
        )
        code = "operation_timeout"
        message = "The backend operation exceeded 30 seconds."
        raise ToolCallError(
            code,
            message,
            {"retryable": True, "action": action},
        ) from exc
    except (RedisError, OSError) as exc:
        code = "backend_error"
        message = (
            "Submission may have been accepted; another submission may duplicate it."
            if name == "lyra_run_metric"
            else "The result backend is temporarily unavailable."
        )
        raise ToolCallError(
            code,
            message,
            {"retryable": True},
        ) from exc


async def _execute_tool(
    name: str,
    arguments: BaseModel,
    backend: LyraMCPBackend,
    *,
    public_api_base_url: str,
) -> BaseModel | dict[str, Any]:
    """Execute one validated tool call against the Lyra domain service.

    Returns:
        The tool-specific success payload.

    Raises:
        ToolCallError: If the requested tool name is unknown.
    """
    if name == "lyra_list_metrics":
        payload = await _list_metrics(cast("ListMetricsInput", arguments), backend)
    elif name == "lyra_search_metrics":
        payload = await _search_metrics(cast("SearchMetricsInput", arguments), backend)
    elif name == "lyra_get_metric":
        payload = await _get_metric(cast("GetMetricInput", arguments), backend)
    elif name == "lyra_get_metric_dependencies":
        discovery = await backend.get_discovery()
        try:
            payload = inspect_dependencies(discovery, cast("GetMetricInput", arguments))
        except (KeyError, ValueError) as exc:
            code = "invalid_metric_selection"
            raise ToolCallError(code, str(exc)) from exc
    elif name == "lyra_run_metric":
        payload = await _run_metric(cast("JobCreateRequest", arguments), backend)
    elif name == "lyra_get_job_result":
        payload = await _get_job_result(cast("GetJobResultInput", arguments), backend)
    elif name == "lyra_download_result":
        payload = await _download_result(
            cast("ResultRefInput", arguments),
            backend,
            public_api_base_url=public_api_base_url,
        )
    else:
        code = "unknown_tool"
        raise ToolCallError(code, f"Unknown Lyra MCP tool: {name}")
    return payload


async def _search_metrics(
    arguments: SearchMetricsInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    discovery = await backend.get_discovery()
    candidates = (
        discovery.search(arguments.query, limit=arguments.limit)
        if normalize_metric_search_tokens(arguments.query)
        else []
    )
    return {
        "query": arguments.query,
        "catalog_fingerprint": discovery.fingerprint,
        "candidates": [_compact_summary(item) for item in candidates],
    }


async def _list_metrics(
    arguments: ListMetricsInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    discovery = await backend.get_discovery()
    fingerprint = discovery.fingerprint
    metrics = discovery.search(limit=len(discovery.catalog.editions.families))
    offset = _metric_page_offset(arguments.cursor, fingerprint, len(metrics))
    page = metrics[offset : offset + arguments.limit]
    next_offset = offset + len(page)
    next_cursor = (
        _encode_metric_cursor(fingerprint, next_offset)
        if next_offset < len(metrics)
        else None
    )
    return {
        "catalog_fingerprint": fingerprint,
        "total_count": len(metrics),
        "metrics": [_compact_summary(metric) for metric in page],
        "next_cursor": next_cursor,
    }


async def _get_metric(
    arguments: GetMetricInput,
    backend: LyraMCPBackend,
) -> dict[str, Any]:
    discovery = await backend.get_discovery()
    try:
        return discovery.inspect(
            arguments.metric, edition=arguments.edition
        ).model_dump(mode="json")
    except KeyError:
        _raise_tool_error("unknown_metric", "Unknown public metric or edition.")


async def _run_metric(
    arguments: JobCreateRequest,
    backend: LyraMCPBackend,
) -> RunMetricOutput:
    job = await backend.create_job(arguments)
    return RunMetricOutput(
        job_id=job.job_id, result_ref=_result_ref_for_job(job.job_id)
    )


async def _observation_for_ref(
    result_ref: str, backend: LyraMCPBackend
) -> tuple[str, JobObservation]:
    job_id = _job_id_from_result_ref(result_ref)
    observation = await backend.observe_job(job_id)
    if observation is None:
        _raise_tool_error(
            "result_not_found",
            "This result reference is unknown or expired.",
            {"job_id": job_id, "result_ref": result_ref},
        )
    if observation.artifact_id is None and observation.snapshot.status == "succeeded":
        _raise_tool_error(
            "result_unavailable",
            "The successful result payload is no longer available.",
            {"job_id": job_id, "result_ref": result_ref},
        )
    return job_id, observation


async def _get_job_result(
    arguments: GetJobResultInput, backend: LyraMCPBackend
) -> BaseModel:
    job_id, observation = await _observation_for_ref(arguments.result_ref, backend)
    return project_observation(job_id, observation)


async def _download_result(
    arguments: ResultRefInput, backend: LyraMCPBackend, *, public_api_base_url: str
) -> DownloadResultOutput:
    job_id, observation = await _observation_for_ref(arguments.result_ref, backend)
    status = observation.snapshot.status
    details: JsonObject = {"job_id": job_id, "result_ref": arguments.result_ref}
    if status in {"queued", "running"}:
        _raise_tool_error(
            "result_not_ready",
            "Inspect again after two seconds.",
            {**details, "poll_after_seconds": 2, "next_tool": "lyra_get_job_result"},
        )
    if status != "succeeded":
        _raise_tool_error(
            "result_not_downloadable",
            "Failed jobs have no downloadable result.",
            details,
        )
    return DownloadResultOutput(
        job_id=job_id,
        result_ref=arguments.result_ref,
        format="json",
        media_type="application/json",
        lifetime=observation.lifetime,
        lyra_api=handoff(public_api_base_url, job_id, "download"),
    )


def _compact_summary(summary: BaseModel) -> dict[str, Any]:
    payload = summary.model_dump(mode="json")
    payload["description"] = _compact_description(payload["description"])
    return payload


def _compact_description(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= _MAX_COMPACT_DESCRIPTION_LENGTH:
        return compact
    return f"{compact[: _MAX_COMPACT_DESCRIPTION_LENGTH - 1]}…"


def _encode_metric_cursor(fingerprint: str, offset: int) -> str:
    payload = json.dumps(
        {
            "fingerprint": fingerprint,
            "offset": offset,
            "version": _METRIC_CURSOR_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _metric_page_offset(
    cursor: str | None,
    fingerprint: str,
    total_count: int,
) -> int:
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        _raise_invalid_metric_cursor("The cursor is malformed.", exc)

    if not isinstance(payload, dict) or set(payload) != {
        "fingerprint",
        "offset",
        "version",
    }:
        _raise_invalid_metric_cursor("The cursor payload is invalid.")
    if payload["version"] != _METRIC_CURSOR_VERSION:
        _raise_invalid_metric_cursor("The cursor version is unsupported.")
    if payload["fingerprint"] != fingerprint:
        _raise_invalid_metric_cursor("The metric catalog changed between pages.")

    offset = payload["offset"]
    if isinstance(offset, bool) or not isinstance(offset, int):
        _raise_invalid_metric_cursor("The cursor offset is invalid.")
    if offset <= 0 or offset >= total_count:
        _raise_invalid_metric_cursor("The cursor offset is outside the catalog.")
    return offset


def _raise_invalid_metric_cursor(
    reason: str,
    cause: Exception | None = None,
) -> NoReturn:
    error = ToolCallError(
        "invalid_cursor",
        "Metric catalog cursor is invalid or no longer current.",
        {
            "reason": reason,
            "action": "Call lyra_list_metrics again without cursor.",
        },
    )
    if cause is None:
        raise error
    raise error from cause


def _result_ref_for_job(job_id: str) -> str:
    return f"lyra://results/{job_id}"


def _job_id_from_result_ref(result_ref: str) -> str:
    match = _RESULT_REF_PATTERN.fullmatch(result_ref)
    if match is None:
        _raise_tool_error(
            "invalid_result_ref",
            "Invalid Lyra result reference. Expected 'lyra://results/{job_id}'.",
            {"result_ref": result_ref},
        )
    return match.group(1)


def _raise_tool_error(
    code: str,
    message: str,
    details: JsonValue = None,
) -> NoReturn:
    raise ToolCallError(code, message, details)
