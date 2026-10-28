"""Small JSON-only adapter over native RQ jobs and registries."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lyra.sdk.models.job import (
    AdminJobDetail,
    FailedJobResult,
    JobLifecycleStatus,
    JobProgress,
    JobRunProvenance,
    JobStatusInfo,
    ResultLifetime,
    TerminalJobResult,
    parse_job_result,
)
from pydantic import ValidationError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry
from rq.serializers import JSONSerializer

if TYPE_CHECKING:
    from lyra.sdk.models.job import JobEnvelope
    from redis import Redis

    from lyra_app.config import JobsConfig
    from lyra_app.registry import MetricRegistryEntry


@dataclass(frozen=True)
class JobObservation:
    """Best-effort retained observation; lifecycle always comes from RQ."""

    snapshot: JobStatusInfo
    result: TerminalJobResult | None = None
    provenance: JobRunProvenance | None = None
    lifetime: ResultLifetime = field(default_factory=ResultLifetime)


def is_terminal_status(status: JobLifecycleStatus) -> bool:
    """Return whether native execution has finished."""
    return status in {"succeeded", "failed"}


class InvalidJobResultError(ValueError):
    """A retained terminal payload violates the result contract."""


class JobStore:
    """Native RQ storage bound to an application connection and settings."""

    def __init__(self, connection: Redis, settings: JobsConfig) -> None:
        """Capture storage dependencies."""
        self.connection = connection
        self.settings = settings

    def enqueue(
        self,
        envelope: JobEnvelope,
        provenance: JobRunProvenance,
        entry: MetricRegistryEntry,
    ) -> None:
        """Enqueue once, without retry, queue-age expiry, or deduplication."""
        settings = self.settings
        queue = Queue(
            entry.queue, connection=self.connection, serializer=JSONSerializer
        )
        queue.enqueue_call(
            "lyra_app.worker.run_metric_task",
            args=(envelope.model_dump(mode="json"),),
            job_id=envelope.job_id,
            timeout=settings.execution_timeout_seconds,
            result_ttl=settings.result_retention_seconds,
            failure_ttl=settings.result_retention_seconds,
            meta={
                "provenance": provenance.model_dump(mode="json"),
                "contract": entry.metric.model_dump(mode="json"),
                "distribution": entry.distribution,
            },
        )

    def _observation(self, job: Job, *, include_result: bool) -> JobObservation | None:
        status = job.get_status(refresh=False)
        states: dict[JobStatus, JobLifecycleStatus] = {
            JobStatus.QUEUED: "queued",
            JobStatus.STARTED: "running",
            JobStatus.FINISHED: "succeeded",
            JobStatus.FAILED: "failed",
        }
        ttl = self.connection.ttl(job.key)
        if status not in states or ttl == -2:
            return None
        provenance = None
        progress = None
        with suppress(ValidationError):
            provenance = JobRunProvenance.model_validate(job.meta.get("provenance"))
        with suppress(ValidationError):
            progress = JobProgress.model_validate(job.meta.get("progress"))
        failure = (
            self._failure_result(job, provenance)
            if status == JobStatus.FAILED
            else None
        )
        result = failure if include_result else None
        if include_result and status == JobStatus.FINISHED:
            result = self._successful_result(job, provenance)
        if provenance is None and result is not None:
            provenance = result.provenance
        return JobObservation(
            snapshot=JobStatusInfo(
                job_id=job.id,
                status=states[status],
                created_at=job.created_at,
                updated_at=job.ended_at or job.started_at or job.created_at,
                started_at=job.started_at,
                completed_at=job.ended_at,
                metric=provenance.metric if provenance else None,
                error=failure.error if failure else None,
                progress=progress,
            ),
            result=result,
            provenance=provenance,
            lifetime=ResultLifetime(expires_in_seconds=ttl if ttl >= 0 else None),
        )

    @staticmethod
    def _failure_result(
        job: Job, provenance: JobRunProvenance | None
    ) -> FailedJobResult:
        payload = job.meta.get("failure")
        if isinstance(payload, dict) and provenance is not None:
            payload = {**payload, "provenance": provenance.model_dump(mode="json")}
        with suppress(ValidationError):
            failure = FailedJobResult.model_validate(payload)
            if failure.job_id == job.id:
                return failure
        return FailedJobResult(
            job_id=job.id,
            provenance=provenance,
            error={"type": "worker", "message": "Job execution failed."},
        )

    @staticmethod
    def _successful_result(
        job: Job, provenance: JobRunProvenance | None
    ) -> TerminalJobResult | None:
        record = job.latest_result()
        if record is None:
            return None
        payload = record.return_value
        if isinstance(payload, dict) and provenance is not None:
            payload = {**payload, "provenance": provenance.model_dump(mode="json")}
        message = "Invalid stored job result."
        try:
            result = parse_job_result(payload)
        except (ValueError, TypeError) as exc:
            raise InvalidJobResultError(message) from exc
        if result.job_id != job.id or result.status != "succeeded":
            raise InvalidJobResultError(message)
        return result

    def read_job(self, job_id: str) -> JobObservation | None:
        """Read native storage without cleanup or record repair.

        Returns:
            None for an absent or expired job.
        """
        try:
            return self._observation(
                Job.fetch(
                    job_id, connection=self.connection, serializer=JSONSerializer
                ),
                include_result=True,
            )
        except NoSuchJobError:
            return None

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Offload synchronous RQ reads from an asynchronous consumer.

        Returns:
            The currently retained observation.
        """
        return await asyncio.to_thread(self.read_job, job_id)

    def read_status(self, job_id: str) -> JobStatusInfo | None:
        """Return lifecycle metadata without loading a successful result."""
        try:
            job = Job.fetch(
                job_id, connection=self.connection, serializer=JSONSerializer
            )
        except NoSuchJobError:
            return None
        observation = self._observation(job, include_result=False)
        return observation.snapshot if observation else None

    async def get_job_status_async(self, job_id: str) -> JobStatusInfo | None:
        """Return lifecycle metadata using an offloaded read."""
        return await asyncio.to_thread(self.read_status, job_id)

    def list_job_statuses(
        self,
        *,
        queue: str,
        status: JobLifecycleStatus,
        offset: int = 0,
        limit: int = 50,
    ) -> list[JobStatusInfo]:
        """Read a queue or registry page without triggering worker maintenance.

        Returns:
            Still-retained matching jobs; transitions may shorten a page.
        """
        connection = self.connection
        if status == "queued":
            ids = Queue(
                queue, connection=connection, serializer=JSONSerializer
            ).get_job_ids(offset=offset, length=limit)
        else:
            registry_class = {
                "running": StartedJobRegistry,
                "succeeded": FinishedJobRegistry,
                "failed": FailedJobRegistry,
            }[status]
            registry = registry_class(
                queue, connection=connection, serializer=JSONSerializer
            )
            ids = registry.get_job_ids(
                start=offset,
                end=offset + limit - 1,
                desc=status in {"succeeded", "failed"},
                cleanup=False,
            )
        snapshots = [self.read_status(job_id) for job_id in ids]
        return [
            item for item in snapshots if item is not None and item.status == status
        ]

    def get_job_detail(self, job_id: str) -> AdminJobDetail | None:
        """Read administrator-only native execution diagnostics.

        Returns:
            A retained job's status, queue, worker identity, and traceback.
        """
        try:
            job = Job.fetch(
                job_id, connection=self.connection, serializer=JSONSerializer
            )
            observation = self._observation(job, include_result=False)
            if observation is None:
                return None
            return AdminJobDetail(
                snapshot=observation.snapshot,
                queue=job.origin,
                worker_id=job.worker_name or job.meta.get("worker_id"),
                failure_diagnostics=(
                    record.exc_string
                    if observation.snapshot.status == "failed"
                    and (record := job.latest_result())
                    else None
                ),
            )
        except NoSuchJobError:
            return None
