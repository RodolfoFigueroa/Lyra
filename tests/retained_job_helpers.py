import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rq.job import JobStatus

from lyra.contracts.jobs import JobRunProvenance, TableJobResult
from lyra.runtime.results import ResultStore, RunMetadata, RunOutputMetadata, RunResult
from lyra.server import job_store
from tests.provenance_helpers import result_provenance
from tests.result_helpers import observed_tables

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def retain(  # ruff: ignore[too-many-arguments] - retained-job fixture controls
    store_path: Path,
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
            reference = "a" * 32
            retained = RunResult(
                tables=tables,
                metadata=RunMetadata(
                    metric_name=JobRunProvenance.model_validate(
                        provenance
                    ).accepted.plan.metric,
                    parameters={},
                    outputs={
                        name: RunOutputMetadata(
                            scope="unpartitioned",
                            table_schema=metadata,
                            owner_scope=None,
                        )
                        for name, metadata in result.output_metadata.items()
                    },
                    store_path=store_path,
                    result_ref=reference,
                ),
            )
            reader = Mock()
            reader.read_result.side_effect = lambda _ref: deepcopy(retained)
            context = Mock(
                __enter__=Mock(return_value=reader), __exit__=Mock(return_value=False)
            )
            monkeypatch.setattr(ResultStore, "open", Mock(return_value=context))
            stored = {"result_ref": reference}
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
