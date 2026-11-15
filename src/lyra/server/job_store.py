"""Small JSON-only adapter over native RQ jobs and registries."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from rq import Queue
from rq.exceptions import DeserializationError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry
from rq.serializers import JSONSerializer

from lyra.contracts.jobs import (
    AdminJobDetail,
    FailedJobResult,
    JobEnvelope,
    JobId,
    JobLifecycleStatus,
    JobProgress,
    JobRunProvenance,
    JobStatusInfo,
    ResultLifetime,
)
from lyra.contracts.references import ExecutionID, ResultReference
from lyra.contracts.strict import StrictBaseModel

if TYPE_CHECKING:
    from redis import Redis

    from lyra.contracts.config import JobsConfig
    from lyra.runtime.results import RunResult


_JOB_ID = TypeAdapter(JobId)


class StoredResult(StrictBaseModel):
    """The complete RQ return value for a successful job."""

    result_ref: ResultReference
    execution_id: ExecutionID


@dataclass(frozen=True)
class JobObservation:
    """Best-effort retained observation; lifecycle always comes from RQ."""

    snapshot: JobStatusInfo
    result_ref: str | None = None
    execution_id: str | None = None
    inspection: dict[str, Any] | None = None
    retained_result: "RunResult | None" = None
    provenance: JobRunProvenance | None = None
    lifetime: ResultLifetime = field(default_factory=ResultLifetime)


def is_terminal_status(status: JobLifecycleStatus) -> bool:
    """Return whether native execution has finished."""
    return status in {"succeeded", "failed"}


class InvalidJobResultError(ValueError):
    """A retained job has an invalid required stored relationship."""


class JobStore:
    """Native RQ storage bound to an application connection and settings."""

    def __init__(self, connection: "Redis", settings: "JobsConfig") -> None:
        """Capture storage dependencies."""
        self.connection = connection
        self.settings = settings

    def enqueue(
        self,
        envelope: JobEnvelope,
        *,
        queue: str,
    ) -> None:
        """Enqueue once, without retry, queue-age expiry, or deduplication."""
        settings = self.settings
        rq_queue = Queue(queue, connection=self.connection, serializer=JSONSerializer)
        rq_queue.enqueue_call(
            "lyra.server.worker.run_metric_task",
            args=(envelope.model_dump(mode="json"),),
            job_id=envelope.job_id,
            timeout=settings.execution_timeout_seconds,
            result_ttl=settings.result_retention_seconds,
            failure_ttl=settings.result_retention_seconds,
            meta={},
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
        if ttl == -2:
            return None
        if status not in states:
            message = "Invalid stored job lifecycle state."
            raise InvalidJobResultError(message)
        progress = None
        try:
            envelope = JobEnvelope.model_validate(job.args[0])
            provenance = JobRunProvenance(
                accepted=envelope.accepted, execution=job.meta.get("execution")
            )
        except (
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            DeserializationError,
        ) as exc:
            message = "Invalid stored job acceptance."
            raise InvalidJobResultError(message) from exc
        if envelope.job_id != job.id:
            message = "Stored job identity disagrees with acceptance"
            raise InvalidJobResultError(message)
        with suppress(ValidationError):
            progress = JobProgress.model_validate(job.meta.get("progress"))
        failure = (
            self._failure_result(job, provenance)
            if status == JobStatus.FAILED
            else None
        )
        stored_result = (
            self._stored_result(job)
            if include_result and status == JobStatus.FINISHED
            else None
        )
        return JobObservation(
            snapshot=JobStatusInfo(
                job_id=job.id,
                status=states[status],
                created_at=job.created_at,
                updated_at=job.ended_at or job.started_at or job.created_at,
                started_at=job.started_at,
                completed_at=job.ended_at,
                metric=provenance.accepted.plan.metric,
                error=failure.error if failure else None,
                progress=progress,
            ),
            result_ref=stored_result.result_ref if stored_result else None,
            execution_id=stored_result.execution_id if stored_result else None,
            provenance=provenance,
            lifetime=ResultLifetime(expires_in_seconds=ttl if ttl >= 0 else None),
        )

    @staticmethod
    def _failure_result(job: Job, provenance: JobRunProvenance) -> FailedJobResult:
        payload = job.meta.get("failure")
        if isinstance(payload, dict):
            payload = {**payload, "provenance": provenance}
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
    def _stored_result(job: Job) -> StoredResult:
        record = job.latest_result()
        if record is None:
            message = "Missing stored job result reference."
            raise InvalidJobResultError(message)
        try:
            return StoredResult.model_validate(record.return_value)
        except ValueError as exc:
            message = "Invalid stored job result reference."
            raise InvalidJobResultError(message) from exc

    def _fetch(self, job_id: str) -> Job | None:
        job_id = _JOB_ID.validate_python(job_id)
        try:
            job = Job.fetch(
                job_id, connection=self.connection, serializer=JSONSerializer
            )
        except NoSuchJobError:
            return None
        except (ValueError, TypeError, KeyError, DeserializationError) as exc:
            message = "Invalid stored job record."
            raise InvalidJobResultError(message) from exc
        if job.id != job_id:
            message = "Stored job identity disagrees with requested identifier"
            raise InvalidJobResultError(message)
        return job

    def read_job(self, job_id: str) -> JobObservation | None:
        """Read retained state without repair.

        Returns:
            The observation, or None for an absent or expired job.
        """
        job = self._fetch(job_id)
        return self._observation(job, include_result=True) if job else None

    def read_status(self, job_id: str) -> JobStatusInfo | None:
        """Return lifecycle metadata without loading a successful result."""
        job = self._fetch(job_id)
        observation = self._observation(job, include_result=False) if job else None
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
        job = self._fetch(job_id)
        if job is None:
            return None
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
