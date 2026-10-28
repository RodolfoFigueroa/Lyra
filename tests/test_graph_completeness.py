"""Cross-layer graph audit using only in-process transports and fake storage."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lyra.sdk import Dependency, Input, MetricParameters, dataset, metric
from lyra.sdk.models.job import JobCreateRequest
from lyra.sdk.models.plugin import TableColumn, TableOutput
from pydantic import Field
from shapely.geometry import Point

from lyra_app import job_store, job_submission, worker
from lyra_app.agent_auth import require_agent_key
from lyra_app.config import JobsConfig
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.mcp.models import TerminalResultOutput
from lyra_app.mcp.results import project_observation
from lyra_app.routes.jobs import router
from lyra_app.runtime import get_jobs
from tests import test_client_policies, test_worker_graph_integration
from tests.test_client_policies import Reply, Scenario, resolve
from tests.test_public_results import retain
from tests.test_worker_graph_integration import install, native_job

scenario = test_client_policies.scenario
clean_state = test_worker_graph_integration.clean_state

COLUMN = TableColumn(
    name="producer_id", type="string", unit=None, description="Source ID"
)
TABLE = TableOutput(columns=[COLUMN])
SPATIAL = TableOutput(columns=[COLUMN], geometry="shape")


class RootParameters(MetricParameters):
    year: int | None = Field(default=2024, description="Requested year")


class SourceParameters(RootParameters):
    count: int = Field(default=2, description="Producer default")


@pytest.mark.parametrize("empty", [False, True])
def test_worker_graph_to_public_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    *,
    empty: bool,
) -> None:
    calls: list[tuple[int | None, int]] = []
    native_tables: list[gpd.GeoDataFrame] = []

    @dataset(
        name="source",
        description="Fixed region source",
        outputs={"places": SPATIAL, "counts": TABLE},
    )
    def source(parameters: SourceParameters) -> dict[str, pd.DataFrame]:
        calls.append((parameters.year, parameters.count))
        plain = pd.DataFrame({"producer_id": ["parcel-z", "parcel-a"]}, index=[42, 42])
        spatial = (
            gpd.GeoDataFrame(plain, geometry=[Point(-99, 19), Point(-98, 20)], crs=4326)
            .rename_geometry("shape")
            .to_crs(6372)
        )
        if empty:
            plain, spatial = plain.iloc[:0], spatial.iloc[:0]
        native_tables.append(spatial)
        return {"places": spatial, "counts": plain}

    @metric(
        name="upstream",
        description="Transform native geometry",
        inputs={
            "data": Dependency(
                "places", arguments={"parameters": {"year": Input("parameters.year")}}
            )
        },
        outputs={"transformed": SPATIAL},
    )
    def upstream(
        parameters: RootParameters, data: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        assert parameters.year is None
        assert data is native_tables[-1]
        assert data.crs == "EPSG:6372"
        assert data.index.tolist() == ([] if empty else [42, 42])
        copied = data.copy()
        copied.geometry = copied.geometry.translate(xoff=100)
        return copied

    @dataset(
        name="branch",
        description="Consume transformed geometry as a table",
        inputs={"data": Dependency("transformed")},
        outputs={"branch_table": SPATIAL},
    )
    def branch(parameters: RootParameters, data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        assert parameters.year is None
        if not empty:
            assert data.geometry.iloc[0].x == pytest.approx(
                native_tables[-1].geometry.iloc[0].x + 100
            )
        return data

    @metric(
        name="root",
        description="Mixed fixed-region result",
        inputs={
            "data": Dependency(
                "branch_table", arguments={"parameters": {"year": None}}
            ),
            "same": Dependency("transformed", arguments={"parameters": {"year": None}}),
            "plain": Dependency("counts", arguments={"parameters": {"year": None}}),
            "different": Dependency("counts"),
        },
        outputs={"parcels": SPATIAL, "summary": TABLE},
    )
    def root(
        parameters: RootParameters,
        data: gpd.GeoDataFrame,
        same: gpd.GeoDataFrame,
        plain: pd.DataFrame,
        different: pd.DataFrame,
    ) -> dict[str, pd.DataFrame]:
        assert parameters.year == 2024
        assert data is same
        assert plain is not different
        return {"parcels": data, "summary": plain}

    _plugins, runtime = install(tmp_path, monkeypatch, [root, upstream, branch, source])
    assert set(worker.RUNNER_REGISTRY) == {"root"}
    assert len(worker.RUNNER_REGISTRY["root"].implementations) == 4
    enqueue = Mock()
    monkeypatch.setattr(runtime.jobs, "enqueue", enqueue)
    database = Mock(spec=ApplicationDatabaseRuntime)
    payload = {}
    for job_id in ("first-job", "job"):
        native = native_job(monkeypatch)
        native.id = job_id
        asyncio.run(
            job_submission.submit_job(
                JobCreateRequest(metric="root", input={"parameters": {}}),
                catalog=runtime.catalog,
                jobs=runtime.jobs,
                database=database,
                job_id_factory=lambda job_id=job_id: job_id,
            )
        )
        envelope, provenance, entry = enqueue.call_args.args
        assert entry.queue == "heavy"
        native.meta["provenance"] = provenance.model_dump(mode="json")
        payload = worker.run_metric_task(envelope.model_dump(mode="json"))
    assert calls == [(None, 2), (2024, 2)] * 2
    assert enqueue.call_count == 2
    database.require_spatial_engine.assert_not_called()
    assert all(table.crs == "EPSG:6372" for table in native_tables)

    verify_public_consumers(monkeypatch, scenario, payload, empty=empty)


def verify_public_consumers(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, payload: dict, *, empty: bool
) -> None:
    app = FastAPI()
    app.include_router(router)
    store = job_store.JobStore(Mock(ttl=Mock(return_value=60)), JobsConfig())
    retain(monkeypatch, payload, jobs=store)
    app.dependency_overrides[get_jobs] = lambda: store
    app.dependency_overrides[require_agent_key] = lambda: None
    with TestClient(app) as http:
        response = http.get("/jobs/job/result")
        assert response.status_code == 200
        assert response.json() == payload
        assert http.get("/jobs/job/result/download").json() == payload
        descriptor = http.get("/jobs/job/result/descriptor").json()
    observation = store.read_job("job")
    assert observation is not None
    inspection = project_observation("job", observation, "https://example.test")
    assert isinstance(inspection, TerminalResultOutput)
    assert set(inspection.outputs) == {"parcels", "summary"}
    for name, output in inspection.outputs.items():
        assert output.metadata is not None
        assert output.metadata.row_count == (0 if empty else 2)
        assert output.metadata.model_dump(mode="json") == descriptor["outputs"][name]
        assert output.preview == payload["outputs"][name]
    for reference in ("job", "lyra://results/job"):
        scenario.replies = [Reply(payload=response.json())]
        tables = resolve(scenario.client.results.tables(reference))
        assert set(tables) == {"parcels", "summary"}
        assert isinstance(tables["parcels"], gpd.GeoDataFrame)
        assert tables["parcels"].crs == "EPSG:4326"
        assert tables["parcels"].geometry.name == "shape"
        for table in tables.values():
            assert table.index.tolist() == ([] if empty else [0, 1])
            assert table.producer_id.tolist() == (
                [] if empty else ["parcel-z", "parcel-a"]
            )
            assert "id" not in table.columns
