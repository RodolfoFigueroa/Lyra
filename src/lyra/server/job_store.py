"""Small JSON-only adapter over native RQ jobs and registries."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
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
)
from lyra.contracts.references import validate_job_id
from lyra.contracts.strict import StrictBaseModel

if TYPE_CHECKING:
    from redis import Redis

    from lyra.contracts.config import JobsConfig
    from lyra.contracts.outputs import OutputMetadata


class StoredResult(StrictBaseModel):
    """The complete RQ return value for a successful job."""

    job_id: JobId


@dataclass(frozen=True)
class JobObservation:
    """Best-effort retained observation; lifecycle always comes from RQ."""

    snapshot: JobStatusInfo
    inspection: dict[str, Any] | None = None
    declarations: dict[str, "OutputMetadata"] = field(default_factory=dict)
    provenance: JobRunProvenance | None = None


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

    @property
    def queue(self) -> Queue:
        """The native JSON queue configured for this deployment."""
        return Queue(
            self.settings.queue, connection=self.connection, serializer=JSONSerializer
        )

    def enqueue(self, envelope: JobEnvelope) -> None:
        """Enqueue once, without retry, queue-age expiry, or deduplication."""
        settings = self.settings
        self.queue.enqueue_call(
            "lyra.server.worker.run_metric_task",
            args=(envelope.model_dump(mode="json"),),
            job_id=envelope.job_id,
            timeout=settings.execution_timeout_seconds,
            result_ttl=settings.rq_retention_seconds,
            failure_ttl=settings.rq_retention_seconds,
            meta={},
        )

    def _observation(self, job: Job) -> JobObservation | None:
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
        if status == JobStatus.FINISHED and self._stored_result(job).job_id != job.id:
            message = "Stored result identity disagrees with accepted job"
            raise InvalidJobResultError(message)
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
            provenance=provenance,
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
        try:
            record = job.latest_result()
            if record is not None:
                return StoredResult.model_validate(record.return_value)
        except (ValueError, TypeError, KeyError, DeserializationError) as exc:
            message = "Invalid stored job completion record."
            raise InvalidJobResultError(message) from exc
        message = "Missing stored job completion record."
        raise InvalidJobResultError(message)

    def _fetch(self, job_id: str) -> Job | None:
        job_id = validate_job_id(job_id)
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
        return self._observation(job) if job else None

    def read_status(self, job_id: str) -> JobStatusInfo | None:
        """Return queue status without reading retained tables or reports."""
        observation = self.read_job(job_id)
        return observation.snapshot if observation else None

    async def get_job_status_async(self, job_id: str) -> JobStatusInfo | None:
        """Return lifecycle metadata using an offloaded read."""
        return await asyncio.to_thread(self.read_status, job_id)

    def list_job_statuses(
        self,
        *,
        status: JobLifecycleStatus,
        offset: int = 0,
        limit: int = 50,
    ) -> list[JobStatusInfo]:
        """Read a queue or registry page without triggering worker maintenance.

        Returns:
            Still-retained matching jobs; transitions may shorten a page.
        """
        queue = self.queue
        if status == "queued":
            ids = queue.get_job_ids(offset=offset, length=limit)
        else:
            registry_class = {
                "running": StartedJobRegistry,
                "succeeded": FinishedJobRegistry,
                "failed": FailedJobRegistry,
            }[status]
            registry = registry_class(queue=queue)
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
        observation = self._observation(job)
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
