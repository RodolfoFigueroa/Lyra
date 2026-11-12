"""FastMCP registration and authenticated application integration."""

from contextvars import ContextVar
from typing import Annotated, Any, TypeVar
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from typing_extensions import override

from lyra.contracts.config import ApiConfig
from lyra.contracts.discovery import MetricInspection
from lyra.contracts.inspection import MAX_INSPECTION_BYTES
from lyra.contracts.jobs import JobCreateRequest, JobId
from lyra.contracts.revisions import CacheRevision
from lyra.contracts.types import JsonValue
from lyra.server.agent_auth import AgentBearerAuthMiddleware
from lyra.server.mcp import tools
from lyra.server.mcp.models import (
    DownloadResultOutput,
    GetJobResultInput,
    GetMetricDependenciesInput,
    GetMetricInput,
    InspectionOutput,
    JobIdInput,
    ListMetricsInput,
    ListMetricsOutput,
    MetricDependenciesOutput,
    RunMetricOutput,
    SearchMetricsInput,
    SearchMetricsOutput,
)
from lyra.server.mcp.serialization import serialize_result, serialized_size

_RAW_ARGUMENTS: ContextVar[dict[str, Any]] = ContextVar("mcp_raw_arguments")


_T = TypeVar("_T", bound=BaseModel)
_EMPTY_OBJECT = Field(default_factory=dict)


def _arguments(model: type[_T]) -> _T:
    # FastMCP pre-parses JSON-looking strings and ignores extra fields. Validate
    # the untouched protocol object with the shared contract before domain work.
    return model.model_validate(_RAW_ARGUMENTS.get())


class LyraFastMCP(FastMCP):
    """Preserve raw shared inputs and bounded domain errors around SDK dispatch."""

    @override
    async def list_tools(self) -> list[Tool]:
        """Advertise the closed argument objects enforced by shared contracts.

        Returns:
            SDK definitions with the shared unknown-field policy.
        """
        definitions = await super().list_tools()
        result = []
        for definition in definitions:
            detached = definition.model_copy(deep=True)
            detached.inputSchema["additionalProperties"] = False
            result.append(detached)
        return result

    @override
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        token = _RAW_ARGUMENTS.set(arguments)
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            if isinstance(exc.__cause__, tools.ToolCallError):
                return serialize_result(
                    exc.__cause__.to_payload(), is_error=True, bounded=True
                )
            error = CallToolResult(
                content=[TextContent(type="text", text=str(exc))], isError=True
            )
            if serialized_size(error) > MAX_INSPECTION_BYTES:
                return serialize_result(
                    {
                        "error": {
                            "code": "result_response_too_large",
                            "message": "The tool error exceeds the size limit.",
                        }
                    },
                    is_error=True,
                )
            raise
        finally:
            _RAW_ARGUMENTS.reset(token)


SERVER_INSTRUCTIONS = (
    "Lyra MCP exposes stable tools for metric discovery, inspection, "
    "metric runs, and result polling. Use lyra_search_metrics with meaningful "
    "task terms when selecting a metric for a specific question. Use "
    "lyra_list_metrics only for explicit catalog-inventory requests or after "
    "focused searches return no candidates. Inspect a selected metric with "
    "lyra_get_metric and submit `metric`, named selections in `inputs`, "
    "and `parameters`. "
    "Each named location input selects collection, unit_type, and nonempty ids. "
    "Lyra converts accepted source selections to the native input through canonical "
    "operations. Named editions require explicit selection, even with one choice. "
    "Pass declared parameters under parameters and optional cache_revision "
    "alongside metric and edition. Preparation errors reject submission; unknown "
    "IDs and unavailable pinned revisions fail accepted jobs. Empty conversions "
    "remain valid selections. Inspecting failed job state succeeds as a tool call. "
    "Metric runs "
    "return plain job_id identifiers. Pass the exact nonempty string to result "
    "tools. Lyra preserves whitespace. Read inspection from structuredContent.result "
    "and submission and download fields directly from structuredContent. "
    "When a job is queued or running, "
    "call lyra_get_job_result every two seconds until terminal, then use "
    "lyra_download_result for authenticated JSON result access. Submission "
    "returns a job ID. Each submission is independent. On a submission "
    "timeout a job may already exist; another submission may duplicate it. "
    "Inspection groups summaries and bounded previews by output name and is "
    "limited to 64 KiB including "
    "both MCP representations. Truncation is explicit; "
    "use the authenticated descriptor URL for complete provenance. Unknown "
    "or expired job IDs return result_not_found. Missing, corrupt, or inconsistent "
    "required stored data produces result_observation_error. Downloads of active "
    "jobs return result_not_ready; downloads of failed jobs return "
    "result_not_downloadable. Invalid arguments produce validation errors "
    "in text content with isError true. "
    "Administrative plugin, "
    "worker, queue, and server-management "
    "operations are not available through MCP."
)

_DEFAULT_ALLOWED_HOSTS = [
    "127.0.0.1:*",
    "localhost:*",
    "testserver",
    "testserver:*",
]


def _allowed_hosts(public_api_base_url: str) -> list[str]:
    hostname = urlsplit(public_api_base_url).hostname
    if hostname is None:  # ApiConfig validation makes this unreachable.
        return list(_DEFAULT_ALLOWED_HOSTS)

    normalized_host = f"[{hostname}]" if ":" in hostname else hostname.lower()
    allowed_hosts = list(_DEFAULT_ALLOWED_HOSTS)
    for candidate in (normalized_host, f"{normalized_host}:*"):
        if candidate not in allowed_hosts:
            allowed_hosts.append(candidate)
    return allowed_hosts


def create_mcp_app(
    *,
    agent_api_key: str,
    public_api_base_url: str,
    name: str = "lyra",
    backend: tools.LyraMCPBackend,
) -> Starlette:
    """Create the authenticated SDK-owned stateless HTTP application.

    Returns:
        The SDK application with bearer authentication and its own lifespan.
    """
    public_api_base_url = ApiConfig(public_base_url=public_api_base_url).public_base_url
    server = LyraFastMCP(
        name=name,
        instructions=SERVER_INSTRUCTIONS,
        json_response=True,
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(public_api_base_url),
            allowed_origins=[],
        ),
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @server.tool(annotations=read_only)
    async def lyra_list_metrics(
        cursor: Annotated[str | None, ListMetricsInput.model_fields["cursor"]] = None,
        limit: Annotated[int, ListMetricsInput.model_fields["limit"]] = 20,
    ) -> ListMetricsOutput:
        """List a compact, paginated inventory of Lyra's public metric catalog.

        Use only when the user explicitly asks which or all metrics are available, or
        after focused searches return no candidates.  Includes public editions and their
        accepted source types. Use `lyra_search_metrics` to select a metric for a
        specific task.

        Returns:
            The requested domain response.
        """
        del cursor, limit
        return await tools.list_metrics(_arguments(ListMetricsInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_search_metrics(
        query: Annotated[str, SearchMetricsInput.model_fields["query"]],
        limit: Annotated[int, SearchMetricsInput.model_fields["limit"]] = 5,
    ) -> SearchMetricsOutput:
        """Search Lyra's public metric catalog for task-specific metric selection.

        Use meaningful task terms rather than empty, single-letter, broad, or inventory
        queries. Returns compact families with matching public editions, accepted source
        types, and declared output granularity.

        Returns:
            The requested domain response.
        """
        del query, limit
        return await tools.search_metrics(_arguments(SearchMetricsInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_get_metric(
        metric: Annotated[str, GetMetricInput.model_fields["metric"]],
        edition: Annotated[str | None, GetMetricInput.model_fields["edition"]] = None,
    ) -> MetricInspection:
        """Inspect a metric family or edition.

        Selected editions include executable bindings.

        Uneditioned families are directly executable.

        Returns:
            The requested domain response.
        """
        del metric, edition
        return await tools.get_metric(_arguments(GetMetricInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_get_metric_dependencies(
        metric: Annotated[str, GetMetricDependenciesInput.model_fields["metric"]],
        edition: Annotated[
            str | None, GetMetricDependenciesInput.model_fields["edition"]
        ] = None,
    ) -> MetricDependenciesOutput:
        """Inspect a concrete metric's dependencies and consumers.

        Use for calculation structure or relationship questions.

        Returns at most 50 relationships in deterministic order with omission counts.
        Location operation outputs use null and receiving inputs use binding paths.
        Routine execution, result interpretation, and errors are self-contained.

        Returns:
            The requested domain response.
        """
        del metric, edition
        return await tools.get_metric_dependencies(
            _arguments(GetMetricDependenciesInput), backend
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )
    )
    async def lyra_run_metric(
        metric: Annotated[str, JobCreateRequest.model_fields["metric"]],
        inputs: Annotated[
            dict[str, JsonValue], JobCreateRequest.model_fields["inputs"]
        ] = _EMPTY_OBJECT,
        parameters: Annotated[
            dict[str, JsonValue], JobCreateRequest.model_fields["parameters"]
        ] = _EMPTY_OBJECT,
        edition: str | None = None,
        cache_revision: CacheRevision | None = None,
    ) -> RunMetricOutput:
        """Submit once with `metric`, `inputs`, and `parameters`.

        Match the contract from `lyra_get_metric`.

        Inspect the returned `job_id` with `lyra_get_job_result`. Each submission
        creates a new job, so a second submission after a timeout may duplicate accepted
        work.

        Returns:
            The requested domain response.
        """
        del metric, inputs, parameters, edition, cache_revision
        return await tools.run_metric(_arguments(JobCreateRequest), backend)

    @server.tool(annotations=read_only)
    async def lyra_get_job_result(
        job_id: JobId,
    ) -> Annotated[CallToolResult, InspectionOutput]:
        """Inspect retained job state once.

        Wait two seconds before polling again for `queued` or `running` jobs.

        The tool call succeeds even when the job failed, because it successfully reports
        the outcome.  Responses group summaries and previews by output name within a 64
        KiB limit. Fetch the descriptor for complete provenance; unknown or expired job
        IDs return `result_not_found`.

        Returns:
            The requested domain response.

        Raises:
            tools.ToolCallError: If mandatory fields exceed the wire budget.
        """
        del job_id
        result = await tools.get_job_result(_arguments(GetJobResultInput), backend)
        response = serialize_result(
            {"result": result.model_dump(mode="json")}, bounded=True
        )
        if response.isError:
            code = "result_response_too_large"
            raise tools.ToolCallError(
                code,
                "The mandatory result exceeds the inspection size limit.",
            )
        return response

    @server.tool(annotations=read_only)
    async def lyra_download_result(job_id: JobId) -> DownloadResultOutput:
        """Return authenticated handoff metadata for downloading complete JSON.

        This does not inline raw data.

        Returns:
            The requested domain response.
        """
        del job_id
        return await tools.download_result(
            _arguments(JobIdInput), backend, public_api_base_url=public_api_base_url
        )

    app = server.streamable_http_app()
    app.add_middleware(AgentBearerAuthMiddleware, agent_api_key=agent_api_key)
    return app
