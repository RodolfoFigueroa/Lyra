import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from rq.job import JobStatus

from lyra.contracts.jobs import TableJobResult
from lyra.runtime.artifacts import ArtifactStore
from lyra.server import job_store
from test_support.workloads import schema
from tests.provenance_helpers import result_provenance
from tests.result_helpers import observed_tables

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
            tables = observed_tables(result)
            schemas = {
                name: pa.DataFrameSchema(
                    {
                        column.name: pa.Column(
                            Geometry(crs=table.crs)
                            if column.dtype == "geometry"
                            else column.dtype,
                            checks=pa.Check(
                                lambda values: values.isna() | values.is_valid,
                                name="valid_geometry",
                            )
                            if column.dtype == "geometry"
                            else None,
                            nullable=column.nullable,
                            coerce=True,
                            description=column.description,
                            metadata={"unit": column.unit},
                        )
                        for column in result.output_metadata[name].columns
                    },
                    strict=True,
                )
                if result.output_metadata[name].columns
                else schema(table)
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
