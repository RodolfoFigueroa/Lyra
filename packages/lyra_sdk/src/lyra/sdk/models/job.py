"""Models for metric job submission, status, and results."""

import math
from datetime import datetime
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Self,
    TypedDict,
    Unpack,
    cast,
)

from lyra.sdk.computation import CacheRevision
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.types import JsonValue
from pydantic import Field, TypeAdapter, model_validator

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

RawResultFormat = Literal["terminal_json"]
"""Raw representation available for retrieving a retained result."""


class JobRunProvenance(StrictBaseModel):
    """Immutable public contract captured when Lyra accepts a job."""

    cache_revision: CacheRevision | None = None
    metric: str = Field(min_length=1, description="Submitted public metric name.")
    catalog_fingerprint: str = Field(
        min_length=1,
        description="Public catalog fingerprint used to validate the submission.",
    )
    plugin: PluginInfo = Field(description="Plugin identity used for the run.")
    input: dict[str, Any] = Field(
        description="Validated unresolved public request submitted by the client.",
    )
    outputs: dict[str, OutputMetadata] = Field(min_length=1)
    created_at: datetime = Field(description="UTC timestamp when the job was created.")


class JobEnvelope(StrictBaseModel):
    """Validated job payload passed from the API to a runner metric."""

    job_id: str = Field(min_length=1, description="Stable job identifier.")
    cache_revision: CacheRevision | None = None
    metric: str = Field(min_length=1, description="Metric name selected by the client.")
    input: dict[str, Any] = Field(description="Validated metric input payload.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional job metadata passed through the runtime.",
    )


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

    kind: Literal["table"] = "table"
    job_id: str = Field(min_length=1)
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
        if self.provenance and self.provenance.outputs != self.output_metadata:
            msg = "output_metadata must match provenance outputs"
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
    job_id: str = Field(min_length=1, description="Job that produced the result.")
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


def parse_job_result(payload: JsonValue) -> TerminalJobResult:
    """Parse a terminal job result payload into the discriminated result union.

    Returns:
        The validated terminal result matching the payload's result kind.
    """
    return _TERMINAL_JOB_RESULT_ADAPTER.validate_python(payload)


def result_ref_for_job(job_id: str) -> str:
    """Return the stable v1 result reference for a job."""
    return f"lyra://results/{job_id}"


class ResultReference(StrictBaseModel):
    """Stable agent-facing reference to a stored terminal job result."""

    job_id: str = Field(min_length=1, description="Job that produced the result.")
    uri: str = Field(min_length=1, description="Stable result reference URI.")

    @classmethod
    def for_job_id(cls, job_id: str) -> Self:
        """Build the stable result reference for a job identifier.

        Args:
            job_id: Identifier of the job that owns the retained result.

        Returns:
            A reference containing the job identifier and its ``lyra://`` URI.

        """
        return cls(job_id=job_id, uri=result_ref_for_job(job_id))

    @model_validator(mode="after")
    def validate_uri(self) -> Self:
        """Validate that the URI is the stable reference for this job.

        Returns:
            The validated reference.

        Raises:
            ValueError: If the URI does not match the job identifier.
        """
        expected = result_ref_for_job(self.job_id)
        if self.uri != expected:
            msg = f"result reference must be {expected!r}"
            raise ValueError(msg)
        return self


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


def _default_raw_result_formats() -> list[RawResultFormat]:
    return ["terminal_json"]


class ResultRawAccess(StrictBaseModel):
    """Metadata for retrieving the raw stored terminal result."""

    result_ref: str = Field(min_length=1, description="Stable result reference URI.")
    formats: list[RawResultFormat] = Field(
        default_factory=_default_raw_result_formats,
        min_length=1,
        description="Raw result formats supported by the server.",
    )
    terminal_json_path: str = Field(
        min_length=1,
        description="HTTP path for the stored terminal result JSON payload.",
    )


class ResultColumnSummary(StrictBaseModel):
    """Observed values; numeric bounds apply only to wholly numeric columns."""

    null_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    minimum: int | float | None = None
    maximum: int | float | None = None


class ResultOutputMetadata(StrictBaseModel):
    """Description and size of one named output, including empty tables."""

    row_count: int = Field(ge=0)
    declaration: OutputMetadata
    spatial: bool
    crs: Literal["EPSG:4326"] | None = None
    geometry_types: list[str] = Field(default_factory=list)
    column_count: int = Field(ge=0)
    summary: dict[str, ResultColumnSummary] = Field(default_factory=dict)


def describe_output(
    value: list[dict[str, JsonValue]] | dict[str, JsonValue],
    declaration: OutputMetadata,
) -> ResultOutputMetadata:
    """Describe public records without coercion or inferred geographic identity.

    Returns:
        Independent column summaries and public geometry metadata.
    """
    spatial = isinstance(value, dict)
    rows = value["features"] if isinstance(value, dict) else value
    rows = cast("list[JsonValue]", rows)
    properties = []
    geometry_types: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if spatial:
            geometry = row.get("geometry")
            if isinstance(geometry, dict) and isinstance(geometry.get("type"), str):
                geometry_types.add(cast("str", geometry["type"]))
        attributes = row.get("properties") if spatial else row
        properties.append(attributes if isinstance(attributes, dict) else {})
    names = dict.fromkeys(
        column.name
        for column in declaration.columns
        if column.name != declaration.geometry
    )
    for row in properties:
        names.update(dict.fromkeys(row))
    summaries = {}
    for name in names:
        values = [row.get(name) for row in properties]
        present = [value for value in values if value is not None]
        numeric = bool(present) and all(
            isinstance(value, int | float) and not isinstance(value, bool)
            for value in present
        )
        numbers = [value for value in present if isinstance(value, int | float)]
        summaries[name] = ResultColumnSummary(
            null_count=len(values) - len(present),
            non_null_count=len(present),
            minimum=min(numbers) if numeric else None,
            maximum=max(numbers) if numeric else None,
        )
    return ResultOutputMetadata(
        row_count=len(rows),
        declaration=declaration,
        spatial=spatial,
        crs="EPSG:4326" if spatial else None,
        geometry_types=sorted(geometry_types),
        column_count=len(names),
        summary=summaries,
    )


class ResultDescriptor(StrictBaseModel):
    """Metadata and complete JSON access for a retained result."""

    job_id: str
    status: TerminalJobStatus
    result_kind: ResultKind
    result_ref: str
    provenance: JobRunProvenance | None = None
    completed_at: datetime
    lifetime: ResultLifetime
    raw: ResultRawAccess
    outputs: dict[str, ResultOutputMetadata] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class ResultDescriptorOptions(TypedDict):
    """Observation metadata supplied by storage."""

    completed_at: datetime
    provenance: NotRequired[JobRunProvenance | None]
    lifetime: NotRequired[ResultLifetime | None]
    terminal_json_path: NotRequired[str | None]


def build_result_descriptor(
    result: TerminalJobResult,
    **options: Unpack[ResultDescriptorOptions],
) -> ResultDescriptor:
    """Describe every named output without projecting it onto request features.

    Returns:
        Metadata and access to the complete result envelope.
    """
    outputs = {}
    if isinstance(result, TableJobResult):
        for name, value in result.outputs.items():
            outputs[name] = describe_output(value, result.output_metadata[name])
    ref = result_ref_for_job(result.job_id)
    return ResultDescriptor(
        job_id=result.job_id,
        status=result.status,
        result_kind=result.kind,
        result_ref=ref,
        provenance=result.provenance or options.get("provenance"),
        completed_at=options["completed_at"],
        lifetime=options.get("lifetime") or ResultLifetime(),
        raw=ResultRawAccess(
            result_ref=ref,
            terminal_json_path=options.get("terminal_json_path")
            or f"/jobs/{result.job_id}/result",
        ),
        outputs=outputs,
        error=result.error if isinstance(result, FailedJobResult) else None,
    )


class JobCreateRequest(StrictBaseModel):
    """Shared request for submitting a job through REST or MCP."""

    cache_revision: CacheRevision | None = None
    metric: str = Field(min_length=1, description="Metric name to execute.")
    input: dict[str, Any] = Field(description="Client payload for the selected metric.")


class JobLinks(StrictBaseModel):
    """Convenience links returned with a newly queued job."""

    self: str = Field(min_length=1, description="URL for the job status resource.")
    result: str = Field(min_length=1, description="URL for the terminal job result.")


class JobCreateResponse(StrictBaseModel):
    """HTTP response returned after a job is accepted."""

    job_id: str = Field(min_length=1, description="Identifier assigned to the job.")
    metric: str = Field(min_length=1, description="Metric accepted for execution.")
    status: Literal["queued"] = Field(description="Initial lifecycle status.")
    links: JobLinks = Field(description="Related job API URLs.")


class JobStatusInfo(StrictBaseModel):
    """Current status snapshot for a queued or completed job."""

    job_id: str = Field(min_length=1, description="Identifier assigned to the job.")
    status: JobLifecycleStatus = Field(description="Current lifecycle status.")
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
