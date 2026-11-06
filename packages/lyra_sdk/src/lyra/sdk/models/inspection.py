"""Shared bounded HTTP and MCP result inspection contracts."""

from datetime import datetime
from typing import Any, Literal

from lyra.sdk.models.job import (
    JobProgress,
    ResultLifetime,
    ResultOutputMetadata,
    ResultOutputStatistics,
)
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

RESULT_REF_PATTERN = r"^lyra://results/[^/?#\s]+$"


class TruncationOutput(StrictBaseModel):
    """Record all omissions from a bounded inspection."""

    omitted_rows: int = Field(default=0, ge=0)
    omitted_columns: int = Field(default=0, ge=0)
    shortened_strings: int = Field(default=0, ge=0)
    omitted_sections: list[str] = Field(default_factory=list)


class CompactProvenance(StrictBaseModel):
    """Retain run identity without geometry or full output declarations."""

    metric: str
    edition: str | None = None
    cache_revision: str | None = None
    catalog_fingerprint: str
    plugin: PluginInfo
    created_at: datetime
    input: dict[str, Any] | None = None


class ActiveResultOutput(StrictBaseModel):
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
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


class StoredOutputInspection(StrictBaseModel):
    """Data-only inspection persisted with one named native output."""

    metadata: ResultOutputStatistics | None
    preview: list[dict[str, Any]] | dict[str, Any] | None
    truncation: TruncationOutput


class ResultOutputInspection(StrictBaseModel):
    """One named public table with explicitly bounded preview data."""

    metadata: ResultOutputMetadata | None
    preview: list[dict[str, Any]] | dict[str, Any] | None
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


class TerminalResultOutput(StrictBaseModel):
    """A compact terminal observation with complete-result access."""

    job_id: str
    result_ref: str = Field(pattern=RESULT_REF_PATTERN)
    status: Literal["succeeded", "failed"]
    result_kind: Literal["table", "failed"]
    metric: str | None = None
    edition: str | None = None
    completed_at: datetime | None = None
    lifetime: ResultLifetime
    download: str
    provenance: CompactProvenance | None = None
    outputs: dict[str, ResultOutputInspection] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    truncation: TruncationOutput = Field(default_factory=TruncationOutput)


ResultInspection = ActiveResultOutput | TerminalResultOutput
