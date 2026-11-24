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
from lyra.contracts.errors import ResultAccessError
from lyra.contracts.inspection import MAX_INSPECTION_BYTES
from lyra.contracts.interventions import InterventionSpec
from lyra.contracts.jobs import JobCreateRequest, JobId
from lyra.contracts.reports import ExecutionReportInspection
from lyra.contracts.results import (
    LocationInputDownload,
    ResultDescriptionInspection,
    ResultRowPage,
    TableDownload,
)
from lyra.contracts.retrieval_paths import (
    absolute_api_url,
    location_input_download_path,
    table_download_path,
)
from lyra.contracts.types import JsonValue
from lyra.server.agent_auth import AgentBearerAuthMiddleware
from lyra.server.mcp import tools
from lyra.server.mcp.models import (
    DescribeResultInput,
    DownloadLocationInputInput,
    DownloadResultOutput,
    DownloadTableInput,
    GetJobResultInput,
    GetLocationInputRowsInput,
    GetMetricDependenciesInput,
    GetMetricInput,
    GetResultRowsInput,
    InspectionOutput,
    InspectReportInput,
    JobIdInput,
    ListMetricsInput,
    ListMetricsOutput,
    MetricDependenciesOutput,
    RunMetricOutput,
    SearchMetricsInput,
    SearchMetricsOutput,
)
from lyra.server.mcp.serialization import (
    mcp_response_budget,
    retrieval_error_result,
    serialize_result,
    serialized_size,
)
from lyra.server.response_budget import ResponseBudget
from lyra.server.result_rows import RowPageContext
from lyra.server.results import inspect_description, inspect_report

_RAW_ARGUMENTS: ContextVar[dict[str, Any]] = ContextVar("mcp_raw_arguments")


_T = TypeVar("_T", bound=BaseModel)
_EMPTY_OBJECT = Field(default_factory=dict)
_EMPTY_ARRAY = Field(default_factory=list)


def _arguments(model: type[_T]) -> _T:
    # FastMCP pre-parses JSON-looking strings and ignores extra fields. Validate
    # the untouched protocol object with the shared contract before domain work.
    return model.model_validate(_RAW_ARGUMENTS.get())


class LyraFastMCP(FastMCP):
    """Preserve raw shared inputs and bounded domain errors around SDK dispatch."""

    def retrieval_budget(self, name: str) -> ResponseBudget[BaseModel]:
        """Return exact response sizing for an active, typed retrieval tool.

        Returns:
            A budget using the registered SDK schema and active request ID.

        Raises:
            ValueError: If the named tool is missing or has no typed output schema.
        """
        tool = self._tool_manager.get_tool(name)
        if tool is None or tool.fn_metadata.output_schema is None:
            msg = "Retrieval sizing requires a registered typed tool."
            raise ValueError(msg)
        return mcp_response_budget(
            tool.fn_metadata, self.get_context().request_context.request_id
        )

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
            if isinstance(exc.__cause__, ResultAccessError):
                return retrieval_error_result(
                    exc.__cause__, self.get_context().request_context.request_id
                )
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
    "lyra_get_metric. Submit the metric name in `metric` and its named selections "
    "in `inputs`. Each selection takes a public provider ID in `provider` and a "
    "list of strings in `ids`. To add or remove records for this run, choose an "
    "entry in `providers` where `membership_capable` is true. Read "
    "`record_schema` for its columns, units, and geometry CRS. "
    "Submit complete addition records and removal IDs in `interventions`. "
    "Change the set ID or revision whenever its contents change. "
    "Lyra converts accepted source selections to the native input through canonical "
    "operations. Select metrics by their registered public names. "
    "Pass declared parameters under parameters. "
    "Preparation errors reject submission; unknown "
    "IDs and unavailable pinned revisions fail accepted jobs. Empty conversions "
    "remain valid selections. Inspecting failed job state succeeds as a tool call. "
    "Metric runs "
    "return plain job_id identifiers. Pass the exact nonempty string to job result "
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
    "Use lyra_describe_result with the durable result_ref for retained metadata "
    "independently of job expiry. Read structuredContent.result.kind: complete "
    "contains the whole description; preview omits it and supplies an authenticated "
    "download_url for complete JSON. Descriptions never load tables or report bodies. "
    "Use lyra_inspect_report with the durable execution_id for a complete report "
    "or fixed preview under structuredContent.result. Preview download_url links "
    "provide the complete authenticated report JSON. Report reads never load tables. "
    "Use lyra_download_table with result_ref and output_name for an authenticated "
    "Arrow IPC stream download_url. Read the link from structuredContent; creating "
    "the link validates the retained output without materializing its rows. "
    "Use lyra_get_location_input_rows with result_ref and input_name for bounded "
    "retained input pages, and lyra_download_location_input for an authenticated "
    "Arrow link. Descriptions include every location input and its availability. "
    "Unavailable inputs return location_input_unavailable; reads never acquire data. "
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
        offset: Annotated[int, ListMetricsInput.model_fields["offset"]] = 0,
        limit: Annotated[int, ListMetricsInput.model_fields["limit"]] = 20,
    ) -> ListMetricsOutput:
        """List a compact, paginated inventory of Lyra's public metric catalog.

        Use only when the user explicitly asks which or all metrics are available, or
        after focused searches return no candidates.  Includes public metrics and their
        accepted source types. Use `lyra_search_metrics` to select a metric for a
        specific task.

        Returns:
            The requested domain response.
        """
        del offset, limit
        return await tools.list_metrics(_arguments(ListMetricsInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_search_metrics(
        query: Annotated[str, SearchMetricsInput.model_fields["query"]],
        limit: Annotated[int, SearchMetricsInput.model_fields["limit"]] = 5,
    ) -> SearchMetricsOutput:
        """Search Lyra's public metric catalog for task-specific metric selection.

        Use meaningful task terms rather than empty, single-letter, broad, or inventory
        queries. Returns matching public metrics, accepted source
        types, and declared output granularity.

        Returns:
            The requested domain response.
        """
        del query, limit
        return await tools.search_metrics(_arguments(SearchMetricsInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_get_metric(
        metric: Annotated[str, GetMetricInput.model_fields["metric"]],
    ) -> MetricInspection:
        """Inspect a registered public metric.

        Descriptions include executable bindings.

        Computed inputs are supplied by dependencies, not callers.

        Returns:
            The requested domain response.
        """
        del metric
        return await tools.get_metric(_arguments(GetMetricInput), backend)

    @server.tool(annotations=read_only)
    async def lyra_get_metric_dependencies(
        metric: Annotated[str, GetMetricDependenciesInput.model_fields["metric"]],
    ) -> MetricDependenciesOutput:
        """Inspect a concrete metric's dependencies and consumers.

        Use for calculation structure or relationship questions.

        Returns at most 50 relationships in deterministic order with omission counts.
        Location operation outputs use null and receiving inputs use binding paths.
        Routine execution, result interpretation, and errors are self-contained.

        Returns:
            The requested domain response.
        """
        del metric
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
        interventions: Annotated[
            list[InterventionSpec], JobCreateRequest.model_fields["interventions"]
        ] = _EMPTY_ARRAY,
    ) -> RunMetricOutput:
        """Submit once with optional inputs, parameters, and inline interventions.

        Match the contract from `lyra_get_metric`.

        Inspect the returned `job_id` with `lyra_get_job_result`. Each submission
        creates a new job, so a second submission after a timeout may duplicate accepted
        work.

        Returns:
            The requested domain response.
        """
        del metric, inputs, parameters, interventions
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

    _register_retained_tools(server, backend, public_api_base_url, read_only)

    app = server.streamable_http_app()
    app.add_middleware(AgentBearerAuthMiddleware, agent_api_key=agent_api_key)
    return app


def _register_retained_tools(
    server: LyraFastMCP,
    backend: tools.LyraMCPBackend,
    public_api_base_url: str,
    read_only: ToolAnnotations,
) -> None:
    """Register typed durable inspections with SDK response sizing."""

    @server.tool(annotations=read_only)
    async def lyra_get_result_rows(
        result_ref: Annotated[str, GetResultRowsInput.model_fields["result_ref"]],
        output_name: Annotated[str, GetResultRowsInput.model_fields["output_name"]],
        limit: Annotated[int, GetResultRowsInput.model_fields["limit"]] = 100,
        cursor: Annotated[str | None, GetResultRowsInput.model_fields["cursor"]] = None,
    ) -> ResultRowPage:
        """Read retained rows with opaque continuation and bounded response bytes.

        Integer cells are decimal strings and geometry cells are WKT strings.
        Continue with next_cursor until null, even after a short page. For
        row_too_large, follow the authenticated Arrow download_url.

        Returns:
            Rows and next_cursor directly in structuredContent.
        """
        del result_ref, output_name, limit, cursor
        arguments = _arguments(GetResultRowsInput)
        return await backend.rows(
            arguments.result_ref,
            arguments.output_name,
            arguments.limit,
            arguments.cursor,
            context=RowPageContext(
                public_api_base_url, server.retrieval_budget("lyra_get_result_rows")
            ),
        )

    @server.tool(annotations=read_only)
    async def lyra_download_table(
        result_ref: Annotated[str, DownloadTableInput.model_fields["result_ref"]],
        output_name: Annotated[str, DownloadTableInput.model_fields["output_name"]],
    ) -> TableDownload:
        """Get a download URL for one retained output.

        Fetch the URL with your agent bearer credential, even after the job
        expires. Lyra checks the stored values before sending the Arrow IPC
        stream. Read the stream with PyArrow or GeoPandas.

        Returns:
            Validated result_ref, output_name, and absolute download_url.
        """
        del result_ref, output_name
        arguments = _arguments(DownloadTableInput)
        await backend.validate_table(arguments.result_ref, arguments.output_name)
        link = TableDownload(
            result_ref=arguments.result_ref,
            output_name=arguments.output_name,
            download_url=absolute_api_url(
                public_api_base_url,
                table_download_path(arguments.result_ref, arguments.output_name),
            ),
        )
        server.retrieval_budget("lyra_download_table").require_fits(link)
        return link

    @server.tool(annotations=read_only)
    async def lyra_get_location_input_rows(
        result_ref: Annotated[
            str, GetLocationInputRowsInput.model_fields["result_ref"]
        ],
        input_name: Annotated[
            str, GetLocationInputRowsInput.model_fields["input_name"]
        ],
        limit: Annotated[int, GetLocationInputRowsInput.model_fields["limit"]] = 100,
        cursor: Annotated[
            str | None, GetLocationInputRowsInput.model_fields["cursor"]
        ] = None,
    ) -> ResultRowPage:
        """Read retained root location-input rows within the complete response budget.

        Returns:
            Complete rows and next_cursor directly in structuredContent.
        """
        del result_ref, input_name, limit, cursor
        arguments = _arguments(GetLocationInputRowsInput)
        return await backend.location_input_rows(
            arguments.result_ref,
            arguments.input_name,
            arguments.limit,
            arguments.cursor,
            context=RowPageContext(
                public_api_base_url,
                server.retrieval_budget("lyra_get_location_input_rows"),
            ),
        )

    @server.tool(annotations=read_only)
    async def lyra_download_location_input(
        result_ref: Annotated[
            str, DownloadLocationInputInput.model_fields["result_ref"]
        ],
        input_name: Annotated[
            str, DownloadLocationInputInput.model_fields["input_name"]
        ],
    ) -> LocationInputDownload:
        """Get a download URL for an available location input.

        Fetch the URL with your agent bearer credential. Lyra checks the stored
        values before sending the Arrow stream.

        Returns:
            Validated result_ref, input_name, and absolute download_url.
        """
        del result_ref, input_name
        arguments = _arguments(DownloadLocationInputInput)
        await backend.validate_location_input(
            arguments.result_ref, arguments.input_name
        )
        link = LocationInputDownload(
            result_ref=arguments.result_ref,
            input_name=arguments.input_name,
            download_url=absolute_api_url(
                public_api_base_url,
                location_input_download_path(
                    arguments.result_ref, arguments.input_name
                ),
            ),
        )
        server.retrieval_budget("lyra_download_location_input").require_fits(link)
        return link

    @server.tool(annotations=read_only)
    async def lyra_describe_result(
        result_ref: Annotated[str, DescribeResultInput.model_fields["result_ref"]],
    ) -> ResultDescriptionInspection:
        """Inspect retained metadata using a durable result_ref, independently of jobs.

        A complete response includes every output and location-input schema,
        plus effective parameters.
        A preview omits the whole description; follow its authenticated download_url
        for complete JSON. Read the variant from structuredContent.result.

        Returns:
            A complete description or fixed preview within the MCP response limit.
        """
        del result_ref
        arguments = _arguments(DescribeResultInput)
        description = await backend.describe_result(arguments.result_ref)
        return inspect_description(
            description,
            public_api_base_url=public_api_base_url,
            budget=server.retrieval_budget("lyra_describe_result"),
        )

    @server.tool(annotations=read_only)
    async def lyra_inspect_report(
        execution_id: Annotated[str, InspectReportInput.model_fields["execution_id"]],
    ) -> ExecutionReportInspection:
        """Inspect a retained execution report independently of job expiry.

        Read structuredContent.result.kind. A complete inspection includes the
        whole report; a preview omits it as a unit and supplies an authenticated
        download_url for complete JSON. Report reads never load output tables.

        Returns:
            A complete report or fixed preview within the MCP response limit.
        """
        del execution_id
        arguments = _arguments(InspectReportInput)
        report = await backend.read_report(arguments.execution_id)
        return inspect_report(
            report,
            public_api_base_url=public_api_base_url,
            budget=server.retrieval_budget("lyra_inspect_report"),
        )
