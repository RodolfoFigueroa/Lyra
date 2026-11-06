"""Data models exposed through Lyra's MCP tools."""

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict, Unpack

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing_extensions import TypeForm

from lyra.contracts.discovery import FamilySummary, MetricInspection
from lyra.contracts.inspection import ResultInspection
from lyra.contracts.jobs import (
    JobCreateRequest,
    ResultLifetime,
)

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


class GetMetricDependenciesInput(GetMetricInput):
    """Select a concrete metric variant for relationship inspection."""

    edition: str | None = Field(
        default=None,
        min_length=1,
        description="Required for families with named editions.",
    )


class DependencyNode(MCPContractModel):
    """Identify a concrete calculation node."""

    name: str
    kind: str
    family: str | None
    edition: str | None
    private: bool
    description: str


class MetricRelationship(MCPContractModel):
    """One declared producer output and receiving input."""

    direction: Literal["dependency", "consumer"]
    producer: DependencyNode
    consumer: DependencyNode
    producer_output: str | None
    consumer_input: str


class MetricDependenciesOutput(MCPContractModel):
    """A bounded direct neighborhood with explicit omission counts."""

    metric: DependencyNode
    relationships: list[MetricRelationship] = Field(max_length=50)
    omitted_relationships: int = Field(ge=0)


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


class BearerAuthenticationOutput(MCPContractModel):
    """Describe the bearer credential needed for an API handoff."""

    scheme: Literal["Bearer"]
    credential_env_var: Literal["LYRA_AGENT_API_KEY"]


class LyraAPIHandoffOutput(MCPContractModel):
    """Describe an authenticated HTTP result download."""

    method: Literal["GET"]
    url: str = Field(pattern=r"^https?://[^/?#]+(?:/[^?#]*)?$")
    authentication: BearerAuthenticationOutput


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
        "lyra_get_metric_dependencies",
        "Inspect a concrete metric's direct declared dependencies and consumers "
        "for calculation structure or relationship questions. Returns at most 50 "
        "relationships in deterministic order with omission counts. Location "
        "operation outputs use null and receiving inputs use binding paths. "
        "Routine execution, result interpretation, and errors are self-contained.",
        GetMetricDependenciesInput,
        MetricDependenciesOutput,
        read_only=True,
        idempotent=True,
    ),
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
        ResultInspection,
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
