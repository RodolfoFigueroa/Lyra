"""Durable terminal history for server-submitted jobs."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

from redis.exceptions import RedisError
from typing_extensions import override

from lyra.contracts.jobs import (
    AdminJobDetail,
    FailedJobResult,
    JobRunProvenance,
    JobStatusInfo,
)
from lyra.contracts.references import validate_job_id
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.runtime.terminal_history import (
    HistoryUnavailableError,
    TerminalOutcome,
    terminal_outcome,
    terminal_statuses,
)
from lyra.server.job_store import (
    InvalidJobResultError,
    JobObservation,
    JobStore,
    is_terminal_status,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from redis import Redis
    from rq.job import Job

    from lyra.contracts.config import JobsConfig
    from lyra.contracts.jobs import JobLifecycleStatus


_Record = TypeVar("_Record", JobObservation, AdminJobDetail)


def job_outcome(
    native: Job,
    provenance: JobRunProvenance,
    *,
    failure: FailedJobResult | None = None,
) -> TerminalOutcome:
    """Return the durable metadata captured at worker completion."""
    completed = (native.ended_at or datetime.now(UTC)).astimezone(UTC)
    snapshot = JobStatusInfo(
        job_id=native.id,
        metric=provenance.accepted.plan.metric,
        status="failed" if failure else "succeeded",
        created_at=provenance.accepted.created_at,
        started_at=native.started_at,
        completed_at=completed,
        updated_at=completed,
        error=failure.error if failure else None,
    )
    return TerminalOutcome(
        AdminJobDetail(
            snapshot=snapshot, queue=native.origin, worker_id=native.worker_name or None
        ),
        provenance,
    )


class ServerJobStore(JobStore):
    """Combine authoritative terminal outcomes with live RQ observations."""

    def __init__(self, connection: Redis, settings: JobsConfig, path: Path) -> None:
        """Bind history to the same result file as the worker."""
        super().__init__(connection, settings)
        self.path = path

    def initialize(self) -> None:
        """Create the shared schema before serving requests."""
        with SQLiteStore(self.path) as store:
            store.initialize()

    def _terminal(self, job_id: str) -> TerminalOutcome | None:
        try:
            return terminal_outcome(self.path, job_id)
        except ValueError as exc:
            message = "Invalid terminal job metadata"
            raise InvalidJobResultError(message) from exc

    def _lookup(
        self,
        job_id: str,
        read_live: Callable[[str], _Record | None],
        project: Callable[[TerminalOutcome], _Record],
    ) -> _Record | None:
        job_id = validate_job_id(job_id)
        if outcome := self._terminal(job_id):
            return project(outcome)
        try:
            live = read_live(job_id)
        except (RedisError, InvalidJobResultError):
            if outcome := self._terminal(job_id):
                return project(outcome)
            raise
        if live and not is_terminal_status(live.snapshot.status):
            return live
        # Publication can commit between the initial SQLite read and the RQ read.
        if outcome := self._terminal(job_id):
            return project(outcome)
        if live is None:
            return None
        if live.snapshot.status == "failed":
            message = "The worker failure is awaiting durable storage."
            raise HistoryUnavailableError(message)
        message = "Missing durable job completion"
        raise InvalidJobResultError(message)

    @override
    def read_job(self, job_id: str) -> JobObservation | None:
        return self._lookup(
            job_id,
            super().read_job,
            lambda outcome: JobObservation(
                snapshot=outcome.detail.snapshot, provenance=outcome.provenance
            ),
        )

    @override
    def list_job_statuses(
        self, *, status: JobLifecycleStatus, offset: int = 0, limit: int = 50
    ) -> list[JobStatusInfo]:
        if not is_terminal_status(status):
            return super().list_job_statuses(status=status, offset=offset, limit=limit)
        return terminal_statuses(self.path, self.settings.queue, status, offset, limit)

    @override
    def get_job_detail(self, job_id: str) -> AdminJobDetail | None:
        return self._lookup(job_id, super().get_job_detail, self._admin_detail)

    def _admin_detail(self, outcome: TerminalOutcome) -> AdminJobDetail:
        detail = outcome.detail
        if detail.snapshot.status == "failed":
            with suppress(RedisError):
                native = self._fetch(detail.snapshot.job_id)
                if native and (record := native.latest_result()):
                    detail.failure_diagnostics = record.exc_string
        return detail
