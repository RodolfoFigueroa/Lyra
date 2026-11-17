"""Data models exposed through Lyra's MCP tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lyra.contracts.discovery import MetricSummary
from lyra.contracts.inspection import ResultInspection
from lyra.contracts.jobs import (
    JobId,
    ResultLifetime,
)
from lyra.contracts.references import RetrievalName
from lyra.contracts.strict import StrictBaseModel

MAX_METRIC_PAGE_SIZE = 20


class InspectReportInput(StrictBaseModel):
    """An exact durable execution ID; domain lookup validates its format."""

    execution_id: str = Field(strict=True)


class DescribeResultInput(StrictBaseModel):
    """An exact durable result reference; domain lookup validates its format."""

    result_ref: str = Field(strict=True)


class DownloadTableInput(DescribeResultInput):
    """Exact retained output name; selection validates its availability."""

    output_name: RetrievalName


class GetResultRowsInput(DownloadTableInput):
    """Typed pagination arguments; semantic validation belongs to the pager."""

    limit: int = Field(default=100, strict=True)
    cursor: str | None = Field(default=None, strict=True)


class DownloadLocationInputInput(DescribeResultInput):
    """Exact retained root location-input name."""

    input_name: RetrievalName


class GetLocationInputRowsInput(DownloadLocationInputInput):
    """Typed input pagination arguments with domain-owned semantic validation."""

    limit: int = Field(default=100, strict=True)
    cursor: str | None = Field(default=None, strict=True)


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
            "descriptions, accepted source types, and output granularity. "
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

    metric: str = Field(min_length=1, description="Registered public metric name.")


class GetMetricDependenciesInput(GetMetricInput):
    """Select a concrete metric variant for relationship inspection."""


class DependencyNode(MCPContractModel):
    """Identify a concrete calculation node."""

    name: str
    kind: str
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


class JobIdInput(MCPContractModel):
    """Identify a retained job using its exact identifier."""

    model_config = ConfigDict(str_strip_whitespace=False)

    job_id: JobId


class GetJobResultInput(JobIdInput):
    """Observe one result without waiting for completion."""


class SearchMetricsOutput(MCPContractModel):
    """Return name-ordered metric candidates and catalog identity."""

    query: str
    catalog_fingerprint: str | None
    candidates: list[MetricSummary]


class ListMetricsOutput(MCPContractModel):
    """Return a catalog page and its continuation cursor."""

    catalog_fingerprint: str = Field(min_length=1)
    total_count: int = Field(ge=0)
    metrics: list[MetricSummary]
    next_cursor: str | None = Field(default=None, min_length=1)


class RunMetricOutput(MCPContractModel):
    """Acknowledge submission without assuming execution status."""

    model_config = ConfigDict(str_strip_whitespace=False)

    job_id: JobId
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

    model_config = ConfigDict(str_strip_whitespace=False)

    job_id: JobId
    status: Literal["succeeded"] = "succeeded"
    format: Literal["json"]
    media_type: str
    lifetime: ResultLifetime
    lyra_api: LyraAPIHandoffOutput


class InspectionOutput(MCPContractModel):
    """Structured inspection envelope including the SDK union wrapper."""

    result: ResultInspection = Field(
        description=(
            "Active, successful, or failed job inspection inside "
            "structuredContent.result."
        )
    )
