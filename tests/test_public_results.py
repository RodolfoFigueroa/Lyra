"""Named results survive native storage, observation, and HTTP unchanged."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lyra.api.client.results import decode_tables
from lyra.sdk.artifacts import ArtifactStore
from lyra.sdk.models.job import TableJobResult, parse_job_result
from lyra.sdk.models.output import ColumnMetadata, OutputMetadata
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.results import serialize_outputs
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from shapely.geometry import Point

from lyra_app import job_store
from lyra_app.agent_auth import require_agent_key
from lyra_app.config import JobsConfig
from lyra_app.results import ResultService
from lyra_app.routes.jobs import router
from lyra_app.runtime import get_jobs, get_results
from tests.provenance_helpers import result_provenance

NOW = datetime(2026, 10, 27, tzinfo=UTC)
COLUMN = ColumnMetadata(name="producer_id", dtype="str", unit=None, description="ID")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_agent_key] = lambda: None
    app.state.jobs = job_store.JobStore(Mock(ttl=Mock(return_value=60)), JobsConfig())
    app.dependency_overrides[get_jobs] = lambda: app.state.jobs
    app.state.artifacts = ArtifactStore(tmp_path)
    app.state.results = ResultService(app.state.jobs, app.state.artifacts)
    app.dependency_overrides[get_results] = lambda: app.state.results
    return TestClient(app)


def result_payload(*, empty: bool, spatial: bool, multiple: bool) -> dict:
    frame = pd.DataFrame({"producer_id": ["parcel-b", "parcel-a"]}, index=[8, 8])
    geo = gpd.GeoDataFrame(frame, geometry=[Point(0, 0), Point(1, 1)], crs=4326)
    if empty:
        frame, geo = frame.iloc[:0], geo.iloc[:0]
    tables = {"parcels": geo if spatial else frame}
    declarations = {
        "parcels": OutputMetadata(
            columns=[
                COLUMN,
                *(
                    [
                        ColumnMetadata(
                            name="geometry",
                            dtype="geometry",
                            description="Geometry",
                            unit=None,
                        )
                    ]
                    if spatial
                    else []
                ),
            ],
            geometry="geometry" if spatial else None,
        )
    }
    if multiple:
        tables["summary"] = pd.DataFrame({"count": [2]})
        declarations["summary"] = OutputMetadata(
            columns=[
                ColumnMetadata(
                    name="count", dtype="int64", unit="count", description="Count"
                )
            ]
        )
    provenance = result_provenance(
        {
            "metric": "root",
            "catalog_fingerprint": "catalog",
            "plugin": PluginInfo(name="plugin", version="1"),
            "input": {
                "location": {
                    "data_type": "geojson",
                    "value": {
                        "type": "FeatureCollection",
                        "features": [{"id": "request-boundary"}],
                    },
                }
            },
            "outputs": declarations,
            "created_at": NOW,
        }
    )
    return TableJobResult.model_validate(
        {
            "job_id": "job",
            "outputs": serialize_outputs("root", tables),
            "output_metadata": declarations,
            "provenance": provenance,
        }
    ).model_dump(mode="json")


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


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("multiple", [False, True])
def test_complete_json_round_trip(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    empty: bool,
    spatial: bool,
    multiple: bool,
) -> None:
    payload = result_payload(empty=empty, spatial=spatial, multiple=multiple)
    retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        payload,
        jobs=cast("FastAPI", client.app).state.jobs,
    )
    response = client.get("/jobs/job/result")
    assert response.status_code == 200
    assert response.json() == payload
    download = client.get("/jobs/job/result/download")
    assert download.status_code == 200
    assert download.json() == response.json()
    assert download.headers["content-type"] == "application/json"
    assert "attachment" in download.headers["content-disposition"]
    descriptor = client.get("/jobs/job/result/inspection").json()
    assert (
        descriptor["provenance"]["catalog_fingerprint"]
        == payload["provenance"]["accepted"]["catalog_fingerprint"]
    )
    assert set(descriptor["outputs"]) == set(payload["outputs"])
    assert descriptor["outputs"]["parcels"]["metadata"]["row_count"] == (
        0 if empty else 2
    )
    assert descriptor["outputs"]["parcels"]["metadata"]["spatial"] == spatial
    assert (
        descriptor["outputs"]["parcels"]["metadata"]["declaration"]["columns"][0][
            "name"
        ]
        == "producer_id"
    )
    assert descriptor["lifetime"]["expires_in_seconds"] == 60
    assert descriptor["download"] == "/jobs/job/result/download"
    assert "request-boundary" not in json.dumps(payload["outputs"])
    if not empty:
        assert "parcel-b" in json.dumps(payload["outputs"])
    assert client.get("/jobs/job").json()["status"] == "succeeded"
    assert client.get("/jobs/job/result/table.jsonl").status_code == 404
    assert "jsonl" not in json.dumps(client.get("/openapi.json").json())


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.STARTED])
def test_unfinished_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status: JobStatus
) -> None:
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        {},
        jobs=cast("FastAPI", client.app).state.jobs,
        status=status,
    )
    assert client.get("/jobs/job/result").status_code == 202
    assert client.get("/jobs/job/result/download").status_code == 202
    assert client.get("/jobs/job/result/inspection").status_code == 202
    job.latest_result.assert_not_called()


def test_failed_result_is_complete_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provenance = result_payload(empty=False, spatial=False, multiple=False)[
        "provenance"
    ]
    payload = {
        "kind": "failed",
        "status": "failed",
        "job_id": "job",
        "error": {"message": "failed node", "node": "source"},
        "provenance": provenance,
    }
    retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        payload,
        jobs=cast("FastAPI", client.app).state.jobs,
        status=JobStatus.FAILED,
    )
    assert client.get("/jobs/job/result").json() == payload
    assert client.get("/jobs/job/result/download").json() == payload
    assert client.get("/jobs/job/result/inspection").json()["outputs"] == {}


@pytest.mark.parametrize("missing", ["job", "expired", "result"])
def test_lifetime_and_missing_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        result_payload(empty=True, spatial=True, multiple=True),
        jobs=cast("FastAPI", client.app).state.jobs,
        ttl=-2 if missing == "expired" else 60,
    )
    if missing == "job":
        monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=NoSuchJobError))
    if missing == "result":
        job.latest_result.return_value = None
    for suffix in ["", "/result", "/result/download", "/result/inspection"]:
        assert client.get(f"/jobs/job{suffix}").status_code == (
            200 if missing == "result" and not suffix else 404
        )


def test_storage_unavailable_never_returns_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        job_store.Job, "fetch", Mock(side_effect=RedisConnectionError("unavailable"))
    )
    with pytest.raises(RedisConnectionError):
        client.get("/jobs/job/result")


@pytest.mark.parametrize("invalid", ["subset", "string", "file", "geojson"])
def test_invalid_result_contract_is_rejected(invalid: str) -> None:
    payload = result_payload(empty=True, spatial=True, multiple=True)
    if invalid == "subset":
        del payload["outputs"]["summary"]
    elif invalid == "string":
        payload["outputs"]["summary"] = "[]"
    elif invalid == "file":
        payload = {
            "kind": "file",
            "job_id": "job",
            "status": "succeeded",
            "file_path": "/private/artifact",
            "media_type": "text/plain",
        }
    else:
        payload["outputs"]["parcels"] = {"type": "Point", "coordinates": [0, 0]}
    with pytest.raises(ValidationError):
        parse_job_result(json.loads(json.dumps(payload)))


@pytest.mark.parametrize("body", [None, {"private": "corrupt"}])
def test_status_and_listing_do_not_load_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: dict | None
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    job = retain(
        cast("FastAPI", client.app).state.artifacts, monkeypatch, body or {}, jobs=store
    )
    job.latest_result.side_effect = AssertionError("status loaded a result")
    job.origin, job.worker_name = "default", "worker"
    assert store.read_status("job").status == "succeeded"
    registry = Mock(get_job_ids=Mock(return_value=["job"]))
    monkeypatch.setattr(job_store, "FinishedJobRegistry", Mock(return_value=registry))
    assert (
        store.list_job_statuses(queue="default", status="succeeded")[0].status
        == "succeeded"
    )
    assert store.get_job_detail("job").snapshot.status == "succeeded"
    job.latest_result.assert_not_called()


@pytest.mark.parametrize("invalid", ["body", "identity", "status", "provenance"])
def test_corrupt_result_preserves_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    payload = result_payload(empty=True, spatial=False, multiple=False)
    job = retain(
        cast("FastAPI", client.app).state.artifacts, monkeypatch, payload, jobs=store
    )
    if invalid == "body":
        job.latest_result.return_value.return_value = {"private": "secret"}
    elif invalid == "identity":
        job.latest_result.return_value.return_value["job_id"] = "another"
    elif invalid == "status":
        job.latest_result.return_value.return_value = {
            "kind": "failed",
            "status": "failed",
            "job_id": "job",
            "error": {},
        }
    else:
        job.latest_result.return_value.return_value["output_metadata"] = {}
    assert client.get("/jobs/job").status_code == 200
    for suffix in ["", "/download", "/inspection"]:
        job.latest_result.reset_mock()
        store.connection.ttl.reset_mock()
        response = client.get(f"/jobs/job/result{suffix}")
        assert response.status_code == 500
        assert response.json()["detail"]["retryable"] is False
        assert "secret" not in response.text
        job.latest_result.assert_called_once_with()
        store.connection.ttl.assert_called_once_with(job.key)


@pytest.mark.parametrize("failure", [None, {}, {"job_id": "another", "error": {}}])
def test_generic_failure_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, failure: dict | None
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        {},
        jobs=store,
        status=JobStatus.FAILED,
    )
    job.meta["failure"] = failure
    observation = store.read_job("job")
    assert observation.snapshot.status == "failed"
    assert observation.snapshot.job_id == "job"
    assert observation.snapshot.error["type"] == "worker"
    job.latest_result.assert_not_called()


@pytest.mark.parametrize("transition", ["completion", "expiry", "missing_result"])
def test_observation_is_sampled_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, transition: str
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        result_payload(empty=True, spatial=False, multiple=False),
        jobs=store,
        status=JobStatus.QUEUED if transition == "completion" else JobStatus.FINISHED,
    )

    def ttl(_key: str) -> int:
        job.get_status.return_value = JobStatus.FINISHED
        job.latest_result.return_value = None
        return -2 if transition == "expiry" else 60

    store.connection.ttl.side_effect = ttl
    observation = store.read_job("job")
    cast("Mock", job_store.Job.fetch).assert_called_once()
    job.get_status.assert_called_once_with(refresh=False)
    store.connection.ttl.assert_called_once_with(job.key)
    assert job.latest_result.call_count == (1 if transition == "missing_result" else 0)
    if transition == "expiry":
        assert observation is None
    else:
        assert observation.snapshot.status == (
            "queued" if transition == "completion" else "succeeded"
        )
        assert observation.artifact_id is None


def test_pending_routes_share_status_and_openapi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        {},
        jobs=store,
        status=JobStatus.STARTED,
    )
    snapshot = client.get("/jobs/job").json()
    schema = client.get("/openapi.json").json()
    for suffix in ["", "/download", "/inspection"]:
        response = client.get(f"/jobs/job/result{suffix}")
        assert response.status_code == 202
        if suffix == "/inspection":
            assert response.json()["status"] == snapshot["status"]
            assert response.json()["job_id"] == snapshot["job_id"]
        else:
            assert response.json() == snapshot
        assert "content-disposition" not in response.headers
        responses = schema["paths"][f"/jobs/{{job_id}}/result{suffix}"]["get"][
            "responses"
        ]
        assert "200" in responses
        assert "202" in responses
    job.latest_result.assert_not_called()


@pytest.mark.parametrize("acceptance", ["valid", "missing", "malformed", "wrong_job"])
def test_provenance_uses_the_authoritative_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, acceptance: str
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    payload = result_payload(empty=True, spatial=False, multiple=False)
    job = retain(
        cast("FastAPI", client.app).state.artifacts, monkeypatch, payload, jobs=store
    )
    if acceptance == "missing":
        job.args = ()
    elif acceptance == "malformed":
        job.args[0]["accepted"] = {"bad": "record"}
    elif acceptance == "wrong_job":
        job.args[0]["job_id"] = "other"
    parse = Mock(wraps=job_store.StoredArtifact.model_validate)
    monkeypatch.setattr(job_store.StoredArtifact, "model_validate", parse)
    if acceptance != "valid":
        with pytest.raises(job_store.InvalidJobResultError):
            store.read_job("job")
        parse.assert_not_called()
        job.latest_result.assert_not_called()
        return
    before = json.dumps(job.args)
    observation = store.read_job("job")
    assert observation.provenance.model_dump(mode="json") == payload["provenance"]
    assert json.dumps(job.args) == before
    assert "provenance" not in job.meta
    assert "provenance" not in job.latest_result.return_value.return_value
    parse.assert_called_once()
    job.latest_result.assert_called_once_with()


def test_failed_admin_details_fetch_diagnostics_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = cast("FastAPI", client.app).state.jobs
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        {},
        jobs=store,
        status=JobStatus.FAILED,
    )
    job.origin, job.worker_name = "default", "worker"
    job.latest_result.return_value = SimpleNamespace(exc_string="traceback")
    assert store.get_job_detail("job").failure_diagnostics == "traceback"
    job.latest_result.assert_called_once_with()


@pytest.mark.parametrize("spatial", [False, True])
def test_preview_bounds_rows_without_mutating_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, spatial: bool
) -> None:
    payload = result_payload(empty=False, spatial=spatial, multiple=False)
    output = payload["outputs"]["parcels"]
    rows = output["features"] if spatial else output
    rows.extend(rows * 5)
    retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        payload,
        jobs=cast("FastAPI", client.app).state.jobs,
    )
    response = client.get("/jobs/job/result/inspection")
    assert response.status_code == 200
    assert response.json()["outputs"]["parcels"]["truncation"]["omitted_rows"] == 7
    preview = response.json()["outputs"]["parcels"]["preview"]
    assert len(preview["features"] if spatial else preview) == 5
    assert client.get("/jobs/job/result").json() == payload


def test_descriptor_preserves_current_execution_observations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = result_payload(empty=False, spatial=False, multiple=False)
    job = retain(
        cast("FastAPI", client.app).state.artifacts,
        monkeypatch,
        payload,
        jobs=cast("FastAPI", client.app).state.jobs,
    )
    execution = {
        "native_inputs": [{"name": "location", "count": 0, "fingerprint": "empty"}],
        "observations": [
            {
                "node": "root",
                "invocation": "root",
                "path": ["root"],
                "outcome": "cache_hit",
            }
        ],
        "succeeded": True,
    }
    job.meta["execution"] = execution
    descriptor = client.get("/jobs/job/result/inspection")
    assert descriptor.status_code == 200
    assert "execution" not in descriptor.json()["provenance"]
    assert (
        client.get("/jobs/job/result/download").json()["provenance"]["execution"]
        == execution
    )
