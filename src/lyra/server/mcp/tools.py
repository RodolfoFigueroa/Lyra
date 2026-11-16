"""MCP tool implementations for querying and operating Lyra."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

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
    DownloadResultOutput,
    ListMetricsOutput,
    RunMetricOutput,
    SearchMetricsOutput,
)
from lyra.server.mcp.results import handoff
from lyra.server.result_projection import project_observation

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from lyra.contracts.discovery import MetricInspection, MetricSummary
    from lyra.contracts.inspection import ResultInspection
    from lyra.contracts.jobs import (
        JobCreateRequest,
        JobCreateResponse,
    )
    from lyra.contracts.reports import ExecutionReport
    from lyra.contracts.results import ResultDescription, ResultRowPage
    from lyra.runtime.planning.discovery import Discovery
    from lyra.server.job_store import JobObservation
    from lyra.server.mcp.models import (
        GetJobResultInput,
        GetMetricDependenciesInput,
        GetMetricInput,
        JobIdInput,
        ListMetricsInput,
        MetricDependenciesOutput,
        SearchMetricsInput,
    )
    from lyra.server.result_rows import RowPageContext
    from lyra.server.runtime import ApplicationRuntime

OPERATION_TIMEOUT_SECONDS = 30.0
_UNKNOWN_METRIC_ERROR = "unknown_metric"
_INVALID_PARAMETERS_ERROR = "invalid_parameters"
_BACKEND_ERROR = "backend_error"
_METRIC_CURSOR_VERSION = 1
_MAX_COMPACT_DESCRIPTION_LENGTH = 240


class LyraMCPBackend(Protocol):
    """Define the domain operations used by transport-independent MCP tools."""

    async def get_discovery(self) -> Discovery:
        """Return the captured public metric projections."""
        ...

    async def create_job(
        self,
        request: JobCreateRequest,
    ) -> JobCreateResponse:
        """Validate and submit a metric job through the Lyra domain service."""
        ...

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Read retained state, raising on broken required result relationships."""
        ...

    async def describe_result(self, result_ref: str) -> ResultDescription:
        """Return complete retained metadata without jobs, tables, or reports."""
        ...

    async def read_report(self, execution_id: str) -> ExecutionReport:
        """Return the complete retained report without jobs, tables, or plugins."""
        ...

    async def rows(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        """Return a page bounded by the active transport envelope."""
        ...

    async def validate_table(self, result_ref: str, output_name: str) -> None:
        """Validate a retained output without materializing table values."""
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
    async def describe_result(self, result_ref: str) -> ResultDescription:
        return await self.runtime().results.describe(result_ref)

    @override
    async def read_report(self, execution_id: str) -> ExecutionReport:
        return await self.runtime().results.report(execution_id)

    @override
    async def rows(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        return await self.runtime().results.rows(
            result_ref,
            output_name,
            limit,
            cursor,
            context=context,
        )

    @override
    async def validate_table(self, result_ref: str, output_name: str) -> None:
        await asyncio.to_thread(
            self.runtime().results.validate_table, result_ref, output_name
        )

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
        except (ValueError, TypeError) as exc:
            code = "result_observation_error"
            message = "The retained job state could not be decoded."
            raise ToolCallError(code, message, {"job_id": job_id}) from exc


@asynccontextmanager
async def _operation_boundary(
    *, timed: bool = False, submission: bool = False
) -> AsyncGenerator[None, None]:
    """Apply backend failure handling and optional deadlines without retrying.

    Raises:
        ToolCallError: For deadline expiry or infrastructure failures.
    """
    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS if timed else None):
            yield
    except TimeoutError as exc:
        action = (
            "Retry observation."
            if not submission
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
            if submission
            else "The result backend is temporarily unavailable."
        )
        raise ToolCallError(
            code,
            message,
            {"retryable": True},
        ) from exc


async def search_metrics(
    arguments: SearchMetricsInput,
    backend: LyraMCPBackend,
) -> SearchMetricsOutput:
    """Return bounded, compact catalog candidates for a lexical query."""
    async with _operation_boundary():
        discovery = await backend.get_discovery()
        candidates = (
            discovery.search(arguments.query, limit=arguments.limit)
            if normalize_metric_search_tokens(arguments.query)
            else []
        )
        return SearchMetricsOutput(
            query=arguments.query,
            catalog_fingerprint=discovery.fingerprint,
            candidates=[_compact_summary(item) for item in candidates],
        )


async def list_metrics(
    arguments: ListMetricsInput,
    backend: LyraMCPBackend,
) -> ListMetricsOutput:
    """Return a bounded catalog page with an opaque continuation cursor."""
    async with _operation_boundary():
        discovery = await backend.get_discovery()
        fingerprint = discovery.fingerprint
        metrics = discovery.search(limit=len(discovery.catalog.graph.nodes))
        offset = _metric_page_offset(arguments.cursor, fingerprint, len(metrics))
        page = metrics[offset : offset + arguments.limit]
        next_offset = offset + len(page)
        next_cursor = (
            _encode_metric_cursor(fingerprint, next_offset)
            if next_offset < len(metrics)
            else None
        )
        return ListMetricsOutput(
            catalog_fingerprint=fingerprint,
            total_count=len(metrics),
            metrics=[_compact_summary(metric) for metric in page],
            next_cursor=next_cursor,
        )


async def get_metric(
    arguments: GetMetricInput,
    backend: LyraMCPBackend,
) -> MetricInspection:
    """Return the detached shared discovery contract for a metric."""
    async with _operation_boundary():
        discovery = await backend.get_discovery()
        try:
            return discovery.inspect(arguments.metric)
        except KeyError:
            _raise_tool_error("unknown_metric", "Unknown public metric.")


async def get_metric_dependencies(
    arguments: GetMetricDependenciesInput, backend: LyraMCPBackend
) -> MetricDependenciesOutput:
    """Return the bounded declared neighborhood of a concrete metric.

    Raises:
        ToolCallError: If the metric selection cannot be resolved.
    """
    async with _operation_boundary():
        discovery = await backend.get_discovery()
        try:
            return inspect_dependencies(discovery, arguments)
        except (KeyError, ValueError) as exc:
            code = "invalid_metric_selection"
            raise ToolCallError(code, str(exc)) from exc


async def run_metric(
    arguments: JobCreateRequest,
    backend: LyraMCPBackend,
) -> RunMetricOutput:
    """Return a new job identifier after one submission, without awaiting execution."""
    async with _operation_boundary(timed=True, submission=True):
        job = await backend.create_job(arguments)
        return RunMetricOutput(job_id=job.job_id)


async def _observation_for_job(
    job_id: str, backend: LyraMCPBackend
) -> tuple[str, JobObservation]:
    observation = await backend.observe_job(job_id)
    if observation is None:
        _raise_tool_error(
            "result_not_found",
            "This job is unknown or expired.",
            {"job_id": job_id},
        )
    return job_id, observation


async def get_job_result(
    arguments: GetJobResultInput, backend: LyraMCPBackend
) -> ResultInspection:
    """Return a detached inspection after observing retained state once."""
    async with _operation_boundary(timed=True):
        job_id, observation = await _observation_for_job(arguments.job_id, backend)
        return project_observation(job_id, observation).model_copy(deep=True)


async def download_result(
    arguments: JobIdInput, backend: LyraMCPBackend, *, public_api_base_url: str
) -> DownloadResultOutput:
    """Return an authenticated download handoff without running the job."""
    async with _operation_boundary(timed=True):
        job_id, observation = await _observation_for_job(arguments.job_id, backend)
        status = observation.snapshot.status
        details: JsonObject = {"job_id": job_id}
        if status in {"queued", "running"}:
            _raise_tool_error(
                "result_not_ready",
                "Inspect again after two seconds.",
                {
                    **details,
                    "poll_after_seconds": 2,
                    "next_tool": "lyra_get_job_result",
                },
            )
        if status != "succeeded":
            _raise_tool_error(
                "result_not_downloadable",
                "Failed jobs have no downloadable result.",
                details,
            )
        return DownloadResultOutput(
            job_id=job_id,
            format="json",
            media_type="application/json",
            lifetime=observation.lifetime.model_copy(deep=True),
            lyra_api=handoff(public_api_base_url, job_id, "download"),
        )


def _compact_summary(summary: MetricSummary) -> MetricSummary:
    return summary.model_copy(
        update={"description": _compact_description(summary.description)}
    )


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


def _raise_tool_error(
    code: str,
    message: str,
    details: JsonValue = None,
) -> NoReturn:
    raise ToolCallError(code, message, details)
