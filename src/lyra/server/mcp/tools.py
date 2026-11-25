"""MCP tool implementations for querying and operating Lyra."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from fastapi.encoders import jsonable_encoder
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

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from lyra.contracts.discovery import MetricInspection, MetricSummary
    from lyra.contracts.inspection import ResultInspection
    from lyra.contracts.jobs import (
        JobCreateRequest,
        JobCreateResponse,
    )
    from lyra.contracts.reports import ExecutionReport
    from lyra.contracts.results import ResultDescription
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
    from lyra.server.runtime import ApplicationRuntime

OPERATION_TIMEOUT_SECONDS = 30.0
_UNKNOWN_METRIC_ERROR = "unknown_metric"
_INVALID_PARAMETERS_ERROR = "invalid_parameters"
_BACKEND_ERROR = "backend_error"
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

    async def inspect_result(self, observation: JobObservation) -> ResultInspection:
        """Build the bounded projection for a queued or terminal job."""
        ...

    async def describe_result(self, job_id: str) -> ResultDescription:
        """Return complete retained metadata without jobs, tables, or reports."""
        ...

    async def read_report(self, job_id: str) -> ExecutionReport:
        """Return the complete retained report without jobs, tables, or plugins."""
        ...

    async def validate_table(self, job_id: str, output_name: str) -> None:
        """Validate a retained output without materializing table values."""
        ...

    async def validate_location_input(self, job_id: str, input_name: str) -> None:
        """Validate retained input lookup and availability before issuing a link."""
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
    async def describe_result(self, job_id: str) -> ResultDescription:
        return await self.runtime().results.describe(job_id)

    @override
    async def read_report(self, job_id: str) -> ExecutionReport:
        return await self.runtime().results.report(job_id)

    @override
    async def validate_table(self, job_id: str, output_name: str) -> None:
        await asyncio.to_thread(
            self.runtime().results.validate_table, job_id, output_name
        )

    @override
    async def validate_location_input(self, job_id: str, input_name: str) -> None:
        await asyncio.to_thread(
            self.runtime().results.validate_location_input, job_id, input_name
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
                validate_json_value(jsonable_encoder(exc.errors)),
            ) from exc
        except SubmissionUnavailableError as exc:
            raise ToolCallError(_BACKEND_ERROR, str(exc), {"retryable": True}) from exc

    @override
    async def inspect_result(self, observation: JobObservation) -> ResultInspection:
        try:
            return await asyncio.to_thread(self.runtime().results.inspect, observation)
        except (ValueError, TypeError) as exc:
            code = "result_observation_error"
            raise ToolCallError(
                code,
                "The retained result could not be decoded.",
                {"job_id": observation.snapshot.job_id},
            ) from exc

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
    """Return a bounded catalog page and its next offset."""
    async with _operation_boundary():
        discovery = await backend.get_discovery()
        fingerprint = discovery.fingerprint
        page, count = discovery.page(offset=arguments.offset, limit=arguments.limit)
        end = arguments.offset + len(page)
        return ListMetricsOutput(
            catalog_fingerprint=fingerprint,
            total_count=count,
            metrics=[_compact_summary(metric) for metric in page],
            next_offset=end if end < count else None,
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


async def _observation_for_job(job_id: str, backend: LyraMCPBackend) -> JobObservation:
    observation = await backend.observe_job(job_id)
    if observation is None:
        _raise_tool_error(
            "result_not_found",
            "This job is unknown.",
            {"job_id": job_id},
        )
    return observation


async def get_job_result(
    arguments: GetJobResultInput, backend: LyraMCPBackend
) -> ResultInspection:
    """Return a detached inspection after observing retained state once."""
    async with _operation_boundary(timed=True):
        observation = await _observation_for_job(arguments.job_id, backend)
        return (await backend.inspect_result(observation)).model_copy(deep=True)


async def download_result(
    arguments: JobIdInput, backend: LyraMCPBackend, *, public_api_base_url: str
) -> DownloadResultOutput:
    """Return an authenticated download handoff without running the job."""
    async with _operation_boundary(timed=True):
        job_id = arguments.job_id
        observation = await _observation_for_job(job_id, backend)
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


def _raise_tool_error(
    code: str,
    message: str,
    details: JsonValue = None,
) -> NoReturn:
    raise ToolCallError(code, message, details)
