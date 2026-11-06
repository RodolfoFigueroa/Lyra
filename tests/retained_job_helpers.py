import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandera.pandas as pa
import pytest
from rq.job import JobStatus

from lyra.api.client._tables import decode_tables  # ruff: ignore[import-private-name]
from lyra.contracts.jobs import TableJobResult
from lyra.runtime.artifacts import ArtifactStore
from lyra.server import job_store
from tests.provenance_helpers import result_provenance

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def retain(  # ruff: ignore[too-many-arguments] - retained-job fixture controls
    artifacts: ArtifactStore,
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
            tables = decode_tables(result)
            schemas = {
                name: pa.DataFrameSchema(
                    {
                        column: pa.Column(series.dtype, nullable=True)
                        for column, series in table.items()
                    }
                )
                for name, table in tables.items()
            }
            stored = {"artifact_id": artifacts.write(tables, schemas)}
    job = SimpleNamespace(
        args=({"job_id": "job", "accepted": provenance["accepted"]},),
        id="job",
        key="rq:job:job",
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
