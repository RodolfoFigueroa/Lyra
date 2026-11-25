"""SQLite publication and lookup of terminal server-job metadata."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from lyra.contracts.jobs import AdminJobDetail, JobRunProvenance, JobStatusInfo
from lyra.contracts.references import validate_job_id
from lyra.runtime.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from lyra.contracts.jobs import JobLifecycleStatus


class HistoryUnavailableError(OSError):
    """The terminal history database cannot be read."""


@contextmanager
def _reader(path: Path) -> Generator[tuple[sa.Connection, sa.Table]]:
    try:
        with SQLiteStore(path, read_only=True) as store, store.engine.connect() as conn:
            yield conn, store.layout.terminal_jobs
    except (SQLAlchemyError, OSError) as exc:
        msg = "Result storage is unavailable."
        raise HistoryUnavailableError(msg) from exc


@dataclass(frozen=True)
class TerminalOutcome:
    """Durable job data shared by detail and result lookups."""

    detail: AdminJobDetail
    provenance: JobRunProvenance

    def publish(self, connection: sa.Connection, table: sa.Table) -> None:
        """Save in the caller's transaction, giving committed results precedence.

        Raises:
            ValueError: If the completion time is missing.
        """
        snapshot = self.detail.snapshot
        if snapshot.completed_at is None:
            msg = "A terminal job requires a completion time"
            raise ValueError(msg)
        values = {
            "job_id": snapshot.job_id,
            "queue": self.detail.queue,
            "worker_id": self.detail.worker_id,
            "status": snapshot.status,
            "completed_at": snapshot.completed_at.isoformat(timespec="microseconds"),
            "snapshot": snapshot.model_dump_json(),
            "provenance": self.provenance.model_dump_json(),
        }
        statement = insert(table)
        if snapshot.status == "failed":
            statement = statement.on_conflict_do_nothing(index_elements=["job_id"])
        else:
            statement = statement.on_conflict_do_update(
                index_elements=["job_id"],
                set_={k: statement.excluded[k] for k in values if k != "job_id"},
            )
        connection.execute(statement, values)


def probe_storage(path: Path) -> None:
    """Read the terminal index without loading payloads."""
    with _reader(path) as (connection, table):
        connection.execute(sa.select(table.c.job_id).limit(1))


def terminal_outcome(path: Path, job_id: str) -> TerminalOutcome | None:
    """Read the terminal record for a job.

    Returns:
        The retained outcome or None for an unknown job.

    Raises:
        ValueError: If the stored metadata is inconsistent.
    """
    job_id = validate_job_id(job_id)
    with _reader(path) as (connection, table):
        row = (
            connection.execute(sa.select(table).where(table.c.job_id == job_id))
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    snapshot = JobStatusInfo.model_validate_json(row["snapshot"])
    provenance = JobRunProvenance.model_validate_json(row["provenance"])
    if (
        snapshot.job_id != job_id
        or snapshot.status != row["status"]
        or snapshot.metric != provenance.accepted.plan.metric
    ):
        msg = "Inconsistent terminal job metadata"
        raise ValueError(msg)
    return TerminalOutcome(
        AdminJobDetail(
            snapshot=snapshot, queue=row["queue"], worker_id=row["worker_id"]
        ),
        provenance,
    )


def terminal_statuses(
    path: Path, queue: str, status: JobLifecycleStatus, offset: int, limit: int
) -> list[JobStatusInfo]:
    """Return one queue's terminal status page in descending completion order."""
    with _reader(path) as (connection, table):
        query = (
            sa.select(table.c.snapshot)
            .where(table.c.queue == queue, table.c.status == status)
            .order_by(table.c.completed_at.desc(), table.c.job_id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [
            JobStatusInfo.model_validate_json(row["snapshot"])
            for row in connection.execute(query).mappings()
        ]
