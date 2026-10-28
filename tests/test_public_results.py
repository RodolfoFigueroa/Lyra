"""Named results survive native storage, observation, and HTTP unchanged."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lyra.sdk.models.job import JobRunProvenance, TableJobResult, parse_job_result
from lyra.sdk.models.plugin import PluginInfo, TableColumn, TableOutput
from lyra.sdk.results import serialize_outputs
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from shapely.geometry import Point

from lyra_app import job_store
from lyra_app.agent_auth import require_agent_key
from lyra_app.config import JobsConfig
from lyra_app.routes.jobs import router
from lyra_app.runtime import get_jobs

NOW = datetime(2026, 10, 27, tzinfo=UTC)
COLUMN = TableColumn(name="producer_id", type="string", unit=None, description="ID")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_agent_key] = lambda: None
    app.state.jobs = job_store.JobStore(Mock(ttl=Mock(return_value=60)), JobsConfig())
    app.dependency_overrides[get_jobs] = lambda: app.state.jobs
    return TestClient(app)


def result_payload(*, empty: bool, spatial: bool, multiple: bool) -> dict:
    frame = pd.DataFrame({"producer_id": ["parcel-b", "parcel-a"]}, index=[8, 8])
    geo = gpd.GeoDataFrame(frame, geometry=[Point(0, 0), Point(1, 1)], crs=4326)
    if empty:
        frame, geo = frame.iloc[:0], geo.iloc[:0]
    tables = {"parcels": geo if spatial else frame}
    declarations = {
        "parcels": TableOutput(
            columns=[COLUMN], geometry="geometry" if spatial else None
        )
    }
    if multiple:
        tables["summary"] = pd.DataFrame({"count": [2]})
        declarations["summary"] = TableOutput(
            columns=[
                TableColumn(
                    name="count", type="integer", unit="count", description="Count"
                )
            ]
        )
    provenance = JobRunProvenance(
        metric="root",
        catalog_fingerprint="catalog",
        plugin=PluginInfo(name="plugin", version="1"),
        input={
            "location": {
                "data_type": "geojson",
                "value": {
                    "type": "FeatureCollection",
                    "features": [{"id": "request-boundary"}],
                },
            }
        },
        outputs=declarations,
        created_at=NOW,
    )
    return TableJobResult.model_validate(
        {
            "job_id": "job",
            "outputs": serialize_outputs("root", tables),
            "output_metadata": declarations,
            "provenance": provenance,
        }
    ).model_dump(mode="json")


def retain(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    *,
    jobs: job_store.JobStore,
    ttl: int = 60,
    status: JobStatus = JobStatus.FINISHED,
) -> SimpleNamespace:
    # JSON round-trip models RQ's JSON serializer without any live service.
    stored = json.loads(json.dumps(payload))
    job = SimpleNamespace(
        id="job",
        key="rq:job:job",
        created_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        meta={
            "provenance": stored.get("provenance"),
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
    retain(monkeypatch, payload, jobs=cast("FastAPI", client.app).state.jobs)
    response = client.get("/jobs/job/result")
    assert response.status_code == 200
    assert response.json() == payload
    download = client.get("/jobs/job/result/download")
    assert download.status_code == 200
    assert download.json() == response.json()
    assert download.headers["content-type"] == "application/json"
    assert "attachment" in download.headers["content-disposition"]
    descriptor = client.get("/jobs/job/result/descriptor").json()
    assert descriptor["provenance"] == payload["provenance"]
    assert set(descriptor["outputs"]) == set(payload["outputs"])
    assert descriptor["outputs"]["parcels"]["row_count"] == (0 if empty else 2)
    assert descriptor["outputs"]["parcels"]["spatial"] == spatial
    assert (
        descriptor["outputs"]["parcels"]["declaration"]["columns"][0]["name"]
        == "producer_id"
    )
    assert descriptor["lifetime"]["expires_in_seconds"] == 60
    assert descriptor["raw"]["formats"] == ["terminal_json"]
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
        monkeypatch, {}, jobs=cast("FastAPI", client.app).state.jobs, status=status
    )
    assert client.get("/jobs/job/result").status_code == 404
    assert client.get("/jobs/job/result/download").status_code == 404
    assert client.get("/jobs/job/result/descriptor").status_code == 202
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
        monkeypatch,
        payload,
        jobs=cast("FastAPI", client.app).state.jobs,
        status=JobStatus.FAILED,
    )
    assert client.get("/jobs/job/result").json() == payload
    assert client.get("/jobs/job/result/download").json() == payload
    assert client.get("/jobs/job/result/descriptor").json()["outputs"] == {}


@pytest.mark.parametrize("missing", ["job", "expired", "result"])
def test_lifetime_and_missing_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    job = retain(
        monkeypatch,
        result_payload(empty=True, spatial=True, multiple=True),
        jobs=cast("FastAPI", client.app).state.jobs,
        ttl=-2 if missing == "expired" else 60,
    )
    if missing == "job":
        monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=NoSuchJobError))
    if missing == "result":
        job.latest_result.return_value = None
    for suffix in ["", "/result", "/result/download", "/result/descriptor"]:
        assert client.get(f"/jobs/job{suffix}").status_code == 404


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
