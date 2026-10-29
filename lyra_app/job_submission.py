"""Job validation, preparation, and submission workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, Unpack
from uuid import uuid4

from lyra.sdk.models.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobLinks,
    JobRunProvenance,
)
from lyra.sdk.models.plugin import PluginInfo
from redis.exceptions import RedisError

from lyra_app.registry import (
    Catalog,
    validate_metric_entry_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.sdk.types import JsonObject

    from lyra_app.job_store import JobStore
    from lyra_app.registry import MetricRegistryEntry


class BuildSubmissionOptions(TypedDict):
    """Identifier for persisted job records."""

    job_id: str


class UnknownMetricError(Exception):
    """Indicate that a submission names no metric in the active catalog."""

    def __init__(self, metric: str) -> None:
        """Initialize the error with the unknown metric name."""
        self.metric = metric
        super().__init__(f"Unknown metric: {metric}")


class SubmissionUnavailableError(Exception):
    """Indicate that submission cannot proceed because Redis is unavailable."""

    def __init__(self) -> None:
        """Initialize the standard temporary-unavailability error."""
        super().__init__(
            "Submission could not be confirmed. "
            "The job may have been accepted; "
            "another submission may duplicate it."
        )


def job_links(job_id: str) -> JobLinks:
    """Build relative API links for a job and its event and result resources.

    Returns:
        The canonical link set rooted at the job resource.
    """
    base = f"/jobs/{job_id}"
    return JobLinks(self=base, result=f"{base}/result")


def _build_submission_records(
    request: JobCreateRequest,
    entry: MetricRegistryEntry,
    validated_input: JsonObject,
    **options: Unpack[BuildSubmissionOptions],
) -> tuple[JobEnvelope, JobRunProvenance]:
    job_id = options["job_id"]
    envelope = JobEnvelope(
        job_id=job_id,
        metric=request.metric,
        input=validated_input,
    )
    provenance = JobRunProvenance(
        metric=request.metric,
        catalog_fingerprint=entry.catalog_fingerprint,
        plugin=PluginInfo(name=entry.plugin_name, version=entry.plugin_version),
        input=validated_input,
        outputs=entry.metric.outputs,
        created_at=datetime.now(UTC),
    )
    return envelope, provenance


async def submit_job(
    request: JobCreateRequest,
    *,
    catalog: Catalog,
    jobs: JobStore,
    job_id_factory: Callable[[], str] | None = None,
) -> JobCreateResponse:
    """Validate request structure before independently enqueueing one job.

    Returns:
        The accepted job identifier and public links.

    Raises:
        UnknownMetricError: If the metric is absent from the installed catalog.
        SubmissionUnavailableError: If enqueueing fails or its outcome is uncertain.
    """
    entry = catalog.get_metric_entry(request.metric)
    if entry is None:
        raise UnknownMetricError(request.metric)
    validated_input = validate_metric_entry_payload(entry, request.input)
    factory = job_id_factory or (lambda: uuid4().hex)
    job_id = factory()
    envelope, provenance = _build_submission_records(
        request,
        entry,
        validated_input,
        job_id=job_id,
    )
    try:
        await asyncio.to_thread(jobs.enqueue, envelope, provenance, entry)
    except RedisError as exc:
        raise SubmissionUnavailableError from exc
    return JobCreateResponse(
        job_id=job_id, metric=request.metric, status="queued", links=job_links(job_id)
    )
