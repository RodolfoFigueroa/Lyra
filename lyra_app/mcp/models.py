"""Data models exposed through Lyra's MCP tools."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict, Unpack

from lyra.sdk.models.job import (
    JobCreateRequest,
    JobProgress,
    ResultLifetime,
    ResultOutputMetadata,
)
from lyra.sdk.models.plugin import (
    PluginInfo,
    TableColumn,
    TableOutput,
)
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
            "descriptions, inputs, and outputs. Do not use empty, single-letter, "
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


class LookupMetZoneInput(MCPContractModel):
    """Resolve a metropolitan zone from a human-readable name."""

    name: str = Field(
        min_length=1,
        description=(
            "Natural-language metropolitan-zone name, including supported "
            "misspellings, to resolve through Lyra's public fuzzy lookup."
        ),
    )


class GetMetricInput(MCPContractModel):
    """Select one metric by its public name."""

    metric: str = Field(min_length=1, description="Public metric name.")


class ResultRefInput(MCPContractModel):
    """Identify a retained result by its stable Lyra reference."""

    result_ref: str = Field(
        pattern=RESULT_REF_PATTERN,
        description="Stable reference shaped lyra://results/{job_id}.",
    )


class GetJobResultInput(ResultRefInput):
    """Observe one result without waiting for completion."""


class SpatialField(MCPContractModel):
    """Identify a spatial request field and its input kind."""

    field: str = Field(min_length=1)
    kind: Literal["location", "bounds"]


class SearchColumn(TableColumn):
    """A matching column with its named output."""

    output: str


class SearchCandidate(MCPContractModel):
    """Describe a metric matched by a catalog search."""

    metric: str = Field(min_length=1)
    description: str
    reason: str = Field(min_length=1)
    required_spatial_fields: list[SpatialField]
    outputs: dict[str, Literal["table"]]
    relevant_columns: list[SearchColumn]


class SearchMetricsOutput(MCPContractModel):
    """Return ranked metric candidates and catalog identity."""

    query: str
    catalog_fingerprint: str | None
    candidates: list[SearchCandidate]


class MetricListItem(MCPContractModel):
    """Summarize one metric in a catalog page."""

    name: str = Field(min_length=1)
    description: str


class ListMetricsOutput(MCPContractModel):
    """Return a catalog page and its continuation cursor."""

    catalog_fingerprint: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    metrics: list[MetricListItem]
    next_cursor: str | None = Field(default=None, min_length=1)


class LookupMetZoneOutput(MCPContractModel):
    """Return the canonical metropolitan-zone code and name."""

    cve_met: str = Field(
        min_length=1,
        description="Canonical metropolitan-zone code accepted by Lyra metrics.",
    )
    nom_met: str = Field(
        min_length=1,
        description="Canonical display name matched by the public fuzzy lookup.",
    )


class GetMetricOutput(MCPContractModel):
    """Return a metric request schema and output contract."""

    name: str = Field(min_length=1)
    description: str
    request_schema: dict[str, Any]
    spatial_inputs: dict[str, Literal["location", "bounds"]]
    outputs: dict[str, TableOutput]


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
        "lyra_lookup_met_zone",
        (
            "Resolve a natural-language metropolitan-zone name or supported "
            "misspelling to its canonical `cve_met` code and `nom_met` display name. "
            "Use `cve_met` as the value of a `met_zone_code` spatial wrapper "
            "in `input`."
        ),
        LookupMetZoneInput,
        LookupMetZoneOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_list_metrics",
        (
            "List a compact, paginated inventory of Lyra's public metric catalog. "
            "Use only when the user explicitly asks which or all metrics are "
            "available, or after focused searches return no candidates.\n\n"
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
            "broad, or inventory queries. Returns candidate reasons, required "
            "spatial fields, output kind, and relevant output columns."
        ),
        SearchMetricsInput,
        SearchMetricsOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_get_metric",
        (
            "Return the public contract for one Lyra metric, including its request "
            "schema, spatial input metadata, and declared output."
        ),
        GetMetricInput,
        GetMetricOutput,
        read_only=True,
        idempotent=True,
    ),
    _contract(
        "lyra_run_metric",
        (
            "Submit once with `metric` and an `input` matching the request schema from "
            "`lyra_get_metric`, then inspect the returned `result_ref` with "
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
