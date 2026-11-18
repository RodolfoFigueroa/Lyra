"""Models for metric job submission, status, and results."""

import math
from copy import deepcopy
from datetime import datetime
from typing import (
    Annotated,
    Any,
    Literal,
    Self,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.interventions import InterventionSpec
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import PluginInfo
from lyra.contracts.provenance import ExecutionObservations
from lyra.contracts.references import ExecutionID, ResultReference
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonValue
from lyra.contracts.value_identity import check_json_value

JobId = Annotated[str, StringConstraints(strict=True, min_length=1)]
"""Nonempty job identifier, preserved exactly, including whitespace.

Whitespace-only strings are valid. Validation imposes no identifier format and
makes no claim that a corresponding job exists.
"""

JobLifecycleStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
]
"""State of a job in Lyra's lifecycle state machine."""

TerminalJobStatus = Literal["succeeded", "failed"]
"""Lifecycle state that prevents any further job transitions."""

ResultKind = Literal["table", "failed"]
"""Discriminator identifying the shape of a terminal job result."""


class AcceptedJob(BaseModel):
    """One accepted plan and its submission context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("plan", "plugin", mode="before")
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    plan: PreparedExecution
    catalog_fingerprint: str = Field(min_length=1)
    plugin: PluginInfo
    created_at: datetime


class JobRunProvenance(BaseModel):
    """Accepted state composed with observations from the current execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("accepted", "execution", mode="before")
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    accepted: AcceptedJob
    execution: ExecutionObservations | None = None


class JobEnvelope(BaseModel):
    """The authoritative accepted job stored in queue arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("accepted", mode="before")
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    job_id: JobId
    accepted: AcceptedJob


class JobProgress(StrictBaseModel):
    """Latest quantitative progress reported by a running metric."""

    timestamp: datetime = Field(description="UTC time of this progress update.")
    stage: str = Field(
        min_length=1,
        max_length=128,
        description="Stable name of the stage being measured.",
    )
    current: int | float = Field(
        ge=0,
        description="Non-negative amount completed in the current stage.",
    )
    total: int | float | None = Field(
        default=None,
        gt=0,
        description="Positive amount that completes the stage, when known.",
    )
    unit: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Human-readable unit for current and total values.",
    )
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Optional concise description of the progress update.",
    )

    @model_validator(mode="after")
    def validate_numbers(self) -> Self:
        """Validate that progress values are finite and internally consistent.

        Returns:
            The validated progress snapshot.

        Raises:
            ValueError: If a value is non-finite or current exceeds total.
        """
        for name, value in (("current", self.current), ("total", self.total)):
            if value is None:
                continue
            if isinstance(value, bool) or not math.isfinite(float(value)):
                msg = f"progress {name} must be a finite number"
                raise ValueError(msg)
        if self.total is not None and self.current > self.total:
            msg = "progress current must not exceed total"
            raise ValueError(msg)
        return self


class TableJobResult(StrictBaseModel):
    """Complete named root outputs and their descriptive declarations."""

    execution_id: ExecutionID
    result_ref: ResultReference
    kind: Literal["table"] = "table"
    job_id: JobId
    status: Literal["succeeded"] = "succeeded"
    outputs: dict[str, list[dict[str, JsonValue]] | dict[str, JsonValue]] = Field(
        min_length=1
    )
    output_metadata: dict[str, OutputMetadata] = Field(min_length=1)
    provenance: JobRunProvenance | None = None

    @model_validator(mode="after")
    def validate_outputs(self) -> Self:
        """Validate envelope shape without validating producer column schemas.

        Returns:
            The complete result.

        Raises:
            ValueError: If metadata keys or spatial output shapes are inconsistent.
        """
        if self.outputs.keys() != self.output_metadata.keys():
            msg = "output_metadata must describe exactly the named outputs"
            raise ValueError(msg)
        if (
            self.provenance
            and self.provenance.accepted.plan.output_metadata != self.output_metadata
        ):
            msg = "output_metadata must match accepted outputs"
            raise ValueError(msg)
        for value in self.outputs.values():
            if isinstance(value, dict) and (
                value.get("type") != "FeatureCollection"
                or not isinstance(value.get("features"), list)
            ):
                msg = "spatial outputs must be GeoJSON FeatureCollections"
                raise ValueError(msg)
        return self


class FailedJobResult(StrictBaseModel):
    """Terminal result for failed jobs."""

    kind: Literal["failed"] = Field(default="failed", description="Result kind.")
    job_id: JobId = Field(description="Job that produced the result.")
    status: Literal["failed"] = Field(
        default="failed",
        description="Terminal job status.",
    )
    error: dict[str, Any] = Field(description="Structured failure details.")
    provenance: JobRunProvenance | None = None


TerminalJobResult = Annotated[
    TableJobResult | FailedJobResult,
    Field(discriminator="kind"),
]
"""Typed terminal result of a job, discriminated by ``kind``."""

_TERMINAL_JOB_RESULT_ADAPTER: TypeAdapter[TerminalJobResult] = TypeAdapter(
    TerminalJobResult
)


def parse_job_result(payload: object) -> TerminalJobResult:
    """Parse a terminal job result payload into the discriminated result union.

    Returns:
        The validated terminal result matching the payload's result kind.
    """
    return _TERMINAL_JOB_RESULT_ADAPTER.validate_python(payload)


class ResultLifetime(StrictBaseModel):
    """Redis-backed lifetime metadata for a stored result."""

    expires_in_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Approximate remaining result lifetime, when available.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Exact result expiry timestamp, when computable.",
    )


class ResultColumnSummary(StrictBaseModel):
    """Observed values; numeric bounds apply only to wholly numeric columns."""

    null_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    minimum: int | float | None = None
    maximum: int | float | None = None


class ResultOutputStatistics(StrictBaseModel):
    """Data-only statistics saved alongside immutable native tables."""

    row_count: int = Field(ge=0)
    spatial: bool
    crs: Literal["EPSG:4326"] | None = None
    geometry_types: list[str] = Field(default_factory=list)
    column_count: int = Field(ge=0)
    summary: dict[str, ResultColumnSummary] = Field(default_factory=dict)


class ResultOutputMetadata(ResultOutputStatistics):
    """Observed statistics with the job's accepted output declaration."""

    declaration: OutputMetadata


class JobCreateRequest(StrictBaseModel):
    """Shared request for submitting a job through REST or MCP."""

    metric: str = Field(min_length=1, description="Metric name to execute.")
    inputs: dict[str, JsonValue] = Field(
        default_factory=dict, description="Named input selections."
    )
    parameters: dict[str, JsonValue] = Field(
        default_factory=dict, description="Calculation parameters."
    )

    interventions: list[InterventionSpec] = Field(
        default_factory=list,
        description="Inline additions and removals by provider ID.",
    )

    @field_validator("metric", "inputs", "parameters", "interventions", mode="before")
    @classmethod
    def validate_sections(cls, value: object) -> object:
        """Validate JSON sections before any coercion.

        Returns:
            The original JSON-compatible section for typed validation.
        """
        check_json_value(value)
        return value


class JobLinks(StrictBaseModel):
    """Convenience links returned with a newly queued job."""

    self: str = Field(min_length=1, description="URL for the job status resource.")
    result: str = Field(min_length=1, description="URL for the terminal job result.")


class JobCreateResponse(StrictBaseModel):
    """HTTP response returned after a job is accepted."""

    job_id: JobId = Field(description="Identifier assigned to the job.")
    metric: str = Field(min_length=1, description="Metric accepted for execution.")
    status: Literal["queued"] = Field(description="Initial lifecycle status.")
    links: JobLinks = Field(description="Related job API URLs.")


class JobStatusInfo(StrictBaseModel):
    """Current status snapshot for a queued or completed job."""

    job_id: JobId = Field(description="Identifier assigned to the job.")
    status: JobLifecycleStatus = Field(description="Current lifecycle status.")
    execution_id: ExecutionID | None = None
    result_ref: ResultReference | None = None
    updated_at: datetime = Field(description="Timestamp for the latest status update.")
    created_at: datetime = Field(description="Time the job was accepted.")
    started_at: datetime | None = Field(
        default=None, description="Time execution was claimed."
    )
    completed_at: datetime | None = Field(
        default=None, description="Time of the terminal lifecycle transition."
    )
    metric: str | None = Field(
        default=None,
        min_length=1,
        description="Metric associated with the job when available.",
    )
    error: dict[str, Any] | None = Field(
        default=None,
        description="Structured failure details when the job failed.",
    )
    progress: JobProgress | None = Field(
        default=None,
        description="Most recently reported quantitative progress.",
    )

    @model_validator(mode="after")
    def validate_retained_identity(self) -> Self:
        """Require both durable identifiers exactly when the job succeeds.

        Returns:
            The validated status snapshot.

        Raises:
            ValueError: If retained identifiers disagree with the lifecycle state.
        """
        succeeded = self.status == "succeeded"
        if any(
            (value is not None) != succeeded
            for value in (self.execution_id, self.result_ref)
        ):
            msg = (
                "Successful jobs require execution_id and result_ref; "
                "other states have none"
            )
            raise ValueError(msg)
        return self


class JobListResponse(StrictBaseModel):
    """Admin response containing a native queue or registry page."""

    jobs: list[JobStatusInfo] = Field(
        description="Retained jobs in native queue or registry order."
    )


class AdminJobDetail(StrictBaseModel):
    """Native job diagnostics available only to administrators."""

    snapshot: JobStatusInfo
    queue: str
    worker_id: str | None = None
    failure_diagnostics: str | None = None
