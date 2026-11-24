"""Job validation, preparation, and submission workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from redis.exceptions import RedisError

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.job_paths import job_path
from lyra.contracts.jobs import (
    AcceptedJob,
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobLinks,
)
from lyra.contracts.references import new_job_id
from lyra.runtime.planning.preparation import PreparationError, prepare_execution

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.contracts.accepted import PreparedExecution
    from lyra.server.job_store import JobStore
    from lyra.server.registry import Catalog


class MetricPayloadValidationError(Exception):
    """Report structured validation failures for a metric request payload."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Initialize the exception with API-compatible validation errors."""
        self.errors = errors
        super().__init__("metric payload validation failed")


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
    base = f"/{job_path(job_id)}"
    return JobLinks(self=base, result=f"{base}/result")


def _build_submission_record(
    catalog: Catalog,
    plan: PreparedExecution,
    **options: Unpack[BuildSubmissionOptions],
) -> JobEnvelope:
    entry = catalog.compiled.graph.nodes[plan.node]
    return JobEnvelope(
        job_id=options["job_id"],
        accepted=AcceptedJob(
            plan=WorkerRequest.from_plan(plan),
            catalog_fingerprint=catalog.discovery.fingerprint,
            plugin=catalog.compiled.manifests[entry.distribution].plugin,
            created_at=datetime.now(UTC),
        ),
    )


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
        MetricPayloadValidationError: If shared preparation rejects the request.
        UnknownMetricError: If the metric is absent from the installed catalog.
        SubmissionUnavailableError: If enqueueing fails or its outcome is uncertain.
    """
    compiled = catalog.compiled
    if request.metric not in compiled.graph.nodes:
        raise UnknownMetricError(request.metric)
    try:
        plan = prepare_execution(compiled, request)
    except PreparationError as exc:
        raise MetricPayloadValidationError(
            [{"loc": [], "msg": str(exc), "type": "value_error"}]
        ) from exc
    factory = job_id_factory or new_job_id
    job_id = factory()
    envelope = _build_submission_record(catalog, plan, job_id=job_id)
    try:
        await asyncio.to_thread(jobs.enqueue, envelope)
    except RedisError as exc:
        raise SubmissionUnavailableError from exc
    return JobCreateResponse(
        job_id=job_id, metric=request.metric, status="queued", links=job_links(job_id)
    )
