import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from rq.job import Job, JobStatus

from lyra.contracts.jobs import FailedJobResult, JobRunProvenance, TableJobResult
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server import job_store
from lyra.server.terminal_jobs import job_outcome
from tests.job_id_helpers import TEST_JOB_ID
from tests.provenance_helpers import result_provenance
from tests.result_helpers import observed_tables

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def retain(
    _store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    *,
    jobs: job_store.JobStore,
    ttl: int = 60,
    status: JobStatus = JobStatus.FINISHED,
) -> SimpleNamespace:
    # JSON round-trip models RQ's JSON serializer without any live service.
    stored = json.loads(json.dumps(payload))
    provenance = stored.pop("provenance", None) or result_provenance(
        {
            "metric": "root",
            "plugin": {"name": "fixture", "version": "1"},
            "outputs": {"result": {"columns": []}},
        }
    ).model_dump(mode="json")
    if status == JobStatus.FINISHED:
        try:
            result = TableJobResult.model_validate(stored)
        except ValueError:
            pass
        else:
            tables = observed_tables(result)
            reference = result.job_id
            reader = Mock()
            reader.job_outputs.side_effect = lambda _ref, _metric: (
                deepcopy(tables),
                deepcopy(result.output_metadata),
            )
            context = Mock(
                __enter__=Mock(return_value=reader), __exit__=Mock(return_value=False)
            )
            monkeypatch.setattr(ResultStore, "open", Mock(return_value=context))
            stored = {
                "job_id": reference,
            }
    job = SimpleNamespace(
        args=(
            {
                "job_id": TEST_JOB_ID,
                "accepted": provenance["accepted"],
            },
        ),
        id=TEST_JOB_ID,
        key=f"rq:job:{TEST_JOB_ID}",
        created_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        meta={
            "execution": provenance.get("execution"),
            "failure": stored if status == JobStatus.FAILED else None,
        },
        get_status=Mock(return_value=status),
        latest_result=Mock(return_value=SimpleNamespace(return_value=stored)),
    )
    monkeypatch.setattr(job_store.Job, "fetch", Mock(return_value=job))
    jobs.connection = Mock(ttl=Mock(return_value=ttl))
    return job


def publish_terminal(path: Path, job: SimpleNamespace) -> None:
    """Finish the worker portion of a simulated RQ lifecycle."""
    provenance = JobRunProvenance(
        accepted=job.args[0]["accepted"], execution=job.meta.get("execution")
    )
    failure = None
    if job.get_status() == JobStatus.FAILED:
        payload = job.meta.get("failure") or {}
        failure = FailedJobResult(
            job_id=job.id,
            error=payload.get(
                "error", {"type": "worker", "message": "Job execution failed."}
            ),
        )
    with SQLiteStore(path) as store, store.transaction() as connection:
        job_outcome(cast("Job", job), provenance, failure=failure).publish(
            connection, store.layout.terminal_jobs
        )
