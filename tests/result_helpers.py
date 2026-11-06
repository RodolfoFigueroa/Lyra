"""Isolated retained-result fixtures using current native artifact boundaries."""

from datetime import UTC, datetime

from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import (
    FailedJobResult,
    JobRunProvenance,
    JobStatusInfo,
    ResultLifetime,
    TableJobResult,
    TerminalJobResult,
)
from lyra.data.inspection import summarize_outputs
from lyra.server.job_store import JobObservation
from lyra.server.result_projection import project_observation
from tests.provenance_helpers import result_provenance


def result_observation(
    *,
    snapshot: JobStatusInfo,
    result: TerminalJobResult | None = None,
    provenance: JobRunProvenance | None = None,
    lifetime: ResultLifetime | None = None,
) -> JobObservation:
    if isinstance(result, FailedJobResult):
        snapshot = snapshot.model_copy(update={"error": result.error})
    if result is not None:
        provenance = result.provenance or provenance
    if isinstance(result, TableJobResult):
        provenance = provenance or result_provenance(
            {
                "metric": snapshot.metric or "fixture",
                "outputs": result.output_metadata,
                "plugin": {"name": "fixture", "version": "1"},
            }
        )
        inspection = summarize_outputs(decode_tables(result))
    else:
        inspection = None
    return JobObservation(
        snapshot=snapshot,
        artifact_id="0" * 32 if inspection is not None else None,
        inspection=inspection,
        provenance=provenance,
        lifetime=lifetime or ResultLifetime(),
    )


def inspection_of(
    result: TableJobResult | FailedJobResult,
    *,
    completed_at: datetime | None = None,
    provenance: JobRunProvenance | None = None,
) -> TerminalResultOutput:
    now = completed_at or datetime.now(UTC)
    inspection = project_observation(
        result.job_id,
        result_observation(
            snapshot=JobStatusInfo(
                job_id=result.job_id,
                status=result.status,
                created_at=now,
                updated_at=now,
                completed_at=now,
            ),
            result=result,
            provenance=provenance,
        ),
    )
    assert isinstance(inspection, TerminalResultOutput)
    return inspection
