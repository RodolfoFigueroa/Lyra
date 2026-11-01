"""Data models exposed through Lyra's MCP tools."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, Unpack

from lyra.sdk.discovery import FamilySummary, MetricInspection
from lyra.sdk.models.job import (
    JobCreateRequest,
    JobProgress,
    ResultLifetime,
    ResultOutputMetadata,
)
from lyra.sdk.models.plugin import PluginInfo
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing_extensions import TypeForm

MAX_METRIC_PAGE_SIZE = 20
RESULT_REF_PATTERN = r"^lyra://results/[^/?#\s]+$"


class MCPContractModel(BaseModel):
    """Strict base for the application's agent-facing MCP contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class SearchMetricsInput(MCPContractModel):
    """Search the metric catalog with a bounded lexical query."""

    query: str = Field(
        min_length=1,
        description=(
            "Meaningful task-specific words to match against metric names, "
            "descriptions, editions, accepted source types, and output granularity. "
            "Do not use empty, single-letter, "
            "or generic inventory queries."
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=MAX_METRIC_PAGE_SIZE,
        description=("Omit this field to use the default of 5 candidates; maximum 20."),
    )


class ListMetricsInput(MCPContractModel):
    """Select a page of the public metric catalog."""

    cursor: str | None = Field(
        default=None,
        min_length=1,
        description="Opaque continuation cursor from the preceding list response.",
    )
    limit: int = Field(
        default=MAX_METRIC_PAGE_SIZE,
        ge=1,
        le=MAX_METRIC_PAGE_SIZE,
        description="Maximum catalog entries to return; defaults to the maximum of 20.",
    )


class GetMetricInput(MCPContractModel):
    """Select one metric by its public name."""

    metric: str = Field(min_length=1, description="Public metric family name.")
    edition: str | None = Field(
        default=None,
        min_length=1,
        description="Select a public edition; omit for the shared family contract.",
    )


class ResultRefInput(MCPContractModel):
    """Identify a retained result by its stable Lyra reference."""

    result_ref: str = Field(
        pattern=RESULT_REF_PATTERN,
        description="Stable reference shaped lyra://results/{job_id}.",
    )


class GetJobResultInput(ResultRefInput):
    """Observe one result without waiting for completion."""


class SearchMetricsOutput(MCPContractModel):
    """Return name-ordered family candidates and catalog identity."""

    query: str
    catalog_fingerprint: str | None
    candidates: list[FamilySummary]


class ListMetricsOutput(MCPContractModel):
    """Return a catalog page and its continuation cursor."""

    catalog_fingerprint: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    metrics: list[FamilySummary]
    next_cursor: str | None = Field(default=None, min_length=1)


class RunMetricOutput(MCPContractModel):
    """Acknowledge submission without assuming execution status."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    next_tool: Literal["lyra_get_job_result"] = "lyra_get_job_result"


class TruncationOutput(MCPContractModel):
    """Record all omissions from a bounded inspection."""

    omitted_rows: int = Field(default=0, ge=0)
    omitted_columns: int = Field(default=0, ge=0)
    shortened_strings: int = Field(default=0, ge=0)
    omitted_sections: list[str] = Field(default_factory=list)


class CompactProvenance(MCPContractModel):
    """Retain run identity without geometry or full output declarations."""

    metric: str
    edition: str | None = None
    cache_revision: str | None = None
    catalog_fingerprint: str
    plugin: PluginInfo
    created_at: datetime
    input: dict[str, Any] | None = None


class ActiveResultOutput(MCPContractModel):
    """Report the actual retained active state."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["queued", "running"]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    metric: str | None = None
    edition: str | None = None
    progress: JobProgress | None = None
    poll_after_seconds: Literal[2] = 2
    next_tool: Literal["lyra_get_job_result"] = "lyra_get_job_result"
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


class BearerAuthenticationOutput(MCPContractModel):
    """Describe the bearer credential needed for an API handoff."""

    scheme: Literal["Bearer"]
    credential_env_var: Literal["LYRA_AGENT_API_KEY"]


class LyraAPIHandoffOutput(MCPContractModel):
    """Describe an authenticated HTTP result download."""

    method: Literal["GET"]
    url: str = Field(pattern=r"^https?://[^/?#]+(?:/[^?#]*)?$")
    authentication: BearerAuthenticationOutput


class ResultOutputInspection(MCPContractModel):
    """One named public table with explicitly bounded preview data."""

    metadata: ResultOutputMetadata | None
    preview: list[dict[str, Any]] | dict[str, Any] | None
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


class TerminalResultOutput(MCPContractModel):
    """A compact terminal observation with complete-descriptor access."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded", "failed"]
    result_kind: Literal["table", "failed"]
    metric: str | None = None
    edition: str | None = None
    completed_at: datetime | None = None
    lifetime: ResultLifetime
    descriptor: LyraAPIHandoffOutput
    provenance: CompactProvenance | None = None
    outputs: dict[str, ResultOutputInspection] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


GetJobResultOutput = ActiveResultOutput | TerminalResultOutput


class DownloadResultOutput(MCPContractModel):
    """Return authenticated download instructions and result lifetime."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded"] = "succeeded"
    format: Literal["json"]
    media_type: str
    lifetime: ResultLifetime
    lyra_api: LyraAPIHandoffOutput


@dataclass(frozen=True)
class ToolContract:
    """Describe one MCP tool's schemas and behavioral annotations."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_adapter: TypeAdapter[BaseModel]
    read_only: bool
    idempotent: bool
    open_world: bool

    @property
    def input_schema(self) -> dict[str, Any]:
        """The JSON Schema generated from the strict input contract."""
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        """The JSON Schema generated for the complete output union."""
        return self.output_adapter.json_schema(mode="serialization")


class ToolBehavior(TypedDict):
    """Behavioral annotations advertised for an MCP tool."""

    read_only: bool
    idempotent: bool
    open_world: NotRequired[bool]


def _contract(
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_type: TypeForm[BaseModel],
    **behavior: Unpack[ToolBehavior],
) -> ToolContract:
    return ToolContract(
        name=name,
        description=description,
        input_model=input_model,
        output_adapter=TypeAdapter(output_type),
        read_only=behavior["read_only"],
        idempotent=behavior["idempotent"],
        open_world=behavior.get("open_world", False),
    )


TOOL_CONTRACTS = (
    _contract(
        "lyra_list_metrics",
        (
            "List a compact, paginated inventory of Lyra's public metric catalog. "
            "Use only when the user explicitly asks which or all metrics are "
            "available, or after focused searches return no candidates.\n\n"
            "Includes public editions and their accepted source types. "
            "Use `lyra_search_metrics` to select a metric for a specific task."
        ),
        ListMetricsInput,
        ListMetricsOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_search_metrics",
        (
            "Search Lyra's public metric catalog for task-specific metric "
            "selection. Use meaningful task terms rather than empty, single-letter, "
            "broad, or inventory queries. Returns compact families with matching "
            "public editions, "
            "accepted source types, and declared output granularity."
        ),
        SearchMetricsInput,
        SearchMetricsOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_get_metric",
        (
            "Inspect a metric family shared contract or select an edition for its "
            "executable bindings. Uneditioned families are directly executable."
        ),
        GetMetricInput,
        MetricInspection,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_run_metric",
        (
            "Submit once with `metric` and an `input` matching the contract from "
            "`lyra_get_metric`. Inspect the returned `result_ref` "
            "with "
            "`lyra_get_job_result`. Each submission creates a new job, so a "
            "second submission after a timeout may duplicate accepted work."
        ),
        JobCreateRequest,
        RunMetricOutput,
        read_only=False,
        idempotent=False,
        open_world=True,
    ),
    _contract(
        "lyra_get_job_result",
        (
            "Inspect the job once, waiting two seconds before polling again if "
            "its status is `queued` or `running`. The tool call succeeds even "
            "when the job failed, because it successfully reports the outcome.\n\n"
            "Responses group summaries and previews by output name within a "
            "64 KiB limit. Fetch the descriptor for complete provenance; "
            "unknown or expired references return `result_not_found`."
        ),
        GetJobResultInput,
        GetJobResultOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_download_result",
        (
            "Return authenticated Lyra API handoff metadata for downloading a table "
            "result as complete JSON. This does not inline raw data."
        ),
        ResultRefInput,
        DownloadResultOutput,
        read_only=True,
        idempotent=True,
    ),
)

TOOL_CONTRACTS_BY_NAME = {contract.name: contract for contract in TOOL_CONTRACTS}
