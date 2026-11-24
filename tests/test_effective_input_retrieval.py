"""Ordinary retained reads of a worker result after its job and catalog change."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pyarrow as pa
import pytest
import shapely
from rq.exceptions import NoSuchJobError
from shapely.geometry import Point

from lyra.contracts.errors import ResultIntegrityError
from lyra.contracts.provider_scope import RecordIdentity
from lyra.runtime.results import ResultStore
from lyra.server import job_store, worker
from lyra.server.main import create_app
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.runtime import ApplicationRuntime
from test_support.mcp import call_tool
from tests.config_helpers import api_credentials
from tests.effective_input_helpers import inputs, workload
from tests.mcp_helpers import mcp_headers
from tests.sqlite_helpers import Database, database
from tests.submission_execution_helpers import submit
from tests.worker_graph_integration_helpers import install_definitions, native_job

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database", "worker_state")

COUNT = 2**60 + 3
POINT = Point(1.2345678901234567, -2.345678901234567)


@pytest.fixture
def expired_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ApplicationRuntime, str]:
    definitions = workload(Mock())
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    runtime.jobs.enqueue = Mock()
    target = {
        "location_id": "001",
        "target_id": "a",
        "geometry": shapely.to_wkt(POINT, rounding_precision=-1),
        "count": str(COUNT),
    }
    changes = [
        {
            "id": "links",
            "catalog": "sources",
            "revision": "1",
            "add": [
                target | {"location_id": key, "target_id": "001", "count": None}
                for key in ("x", "y")
            ],
        },
        {
            "id": "survey",
            "catalog": "targets",
            "revision": "2",
            "add": [target, target | {"location_id": "unused"}],
            "remove": ["b"],
        },
    ]
    assert asyncio.run(
        submit(
            runtime,
            "http",
            {
                "metric": "root",
                "inputs": inputs("x", "a", "y"),
                "interventions": changes,
            },
        )
    )
    envelope = runtime.jobs.enqueue.call_args.args[0]
    native_job(monkeypatch).id = envelope.job_id
    saved = worker.run_metric_task(envelope.model_dump(mode="json"))
    runtime.config.result_store_path.touch()

    for provider in definitions["test"].resources.values():
        monkeypatch.setattr(provider, "code_revision", "changed")
        monkeypatch.setattr(
            provider, "setup", Mock(side_effect=AssertionError("acquisition"))
        )
    _, restarted = install_definitions(tmp_path, monkeypatch, definitions)
    monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=NoSuchJobError))
    assert restarted.jobs.read_job(envelope.job_id) is None
    return restarted, str(saved["job_id"])


def check_pairs(frame: gpd.GeoDataFrame) -> None:
    assert frame.crs == "EPSG:4326"
    assert set(frame.columns) == {
        "source_location_id",
        "location_id",
        "geometry",
        "count",
        "target_id",
    }
    ordered = frame.set_index("source_location_id").sort_index()
    assert ordered.index.tolist() == ["a", "x", "y"]
    assert ordered.location_id.tolist() == ["a", "001", "001"]
    assert ordered["count"].tolist() == [1, COUNT, COUNT]
    assert ordered.geometry.tolist() == [Point(0, 0), POINT, POINT]


def test_reopened_frames_and_exports(
    expired_result: tuple[ApplicationRuntime, str], tmp_path: Path, database: Database
) -> None:
    runtime, ref = expired_result
    path = tmp_path / "pairs.parquet"
    with ResultStore.open(runtime.config.result_store_path) as reader:
        description = reader.describe_result(ref)
        source_scope = description.location_inputs["source"].provider_scope
        linked = description.location_inputs["linked"]
        assert linked.source_scope == source_scope
        assert linked.provider_scope.code_revision is None
        assert description.outputs["rows"].owner_scope is None
        result = reader.read_result(ref)
        assert result.tables["rows"].value.tolist() == [2 * COUNT + 1]
        scopes = {
            v.scope.provider_id: v.scope for v in result.report.intervention_validations
        }
        assert source_scope == scopes["sources"]
        assert linked.provider_scope == scopes["targets"]
        direct = reader.read_location_input(ref, "source").set_index("location_id")
        assert direct.target_id.to_dict() == {"a": "a", "x": "001", "y": "001"}
        assert direct["count"].isna().to_dict() == {"a": False, "x": True, "y": True}
        edited = reader.read_location_input(ref, "linked")
        check_pairs(edited)
        edited.loc[:, "count"] = 0
        edited.attrs["lyra"] = {"description": "changed"}
        destination_description = linked.model_dump(mode="json")
        linked.table_schema.columns.clear()
        reader.export_location_input_geoparquet(ref, "linked", path)
        exported = gpd.read_parquet(path)
        check_pairs(exported)
        assert exported.attrs["lyra"] == {
            "kind": "location_input",
            "job_id": ref,
            "input_name": "linked",
            "description": destination_description,
        }
        check_pairs(reader.read_location_input(ref, "linked"))

        # The accepted request still contains this addition, but it cannot
        # substitute for a missing record required by an available selection.
        key = RecordIdentity(scope=linked.provider_scope, location_id="001").key
        headers = database.rows["stored_results"]
        headers.remove(next(row for row in headers if row["value_key"] == key))
        before = path.read_bytes()
        with pytest.raises(ResultIntegrityError) as failure:
            reader.export_location_input_geoparquet(ref, "linked", path)
        assert failure.value.input_name == "linked"
        assert path.read_bytes() == before


@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.anyio
async def test_effective_descriptions_pages_and_arrow(
    expired_result: tuple[ApplicationRuntime, str],
) -> None:
    runtime, ref = expired_result
    app = create_app(runtime.config, credentials=api_credentials())
    app.state.runtime = runtime
    backend = InProcessLyraBackend(lambda: runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=mcp_headers(),
    ) as http:
        response = await http.get(f"/results/{ref}")
        response.raise_for_status()
        inspection = response.json()
        mcp = await call_tool(backend, "lyra_describe_result", {"job_id": ref})
        assert mcp["structuredContent"]["result"] == inspection
        linked = inspection["description"]["location_inputs"]["linked"]
        assert linked["source_scope"]["intervention"] == {
            "catalog": "sources",
            "id": "links",
            "revision": "1",
        }
        assert linked["provider_scope"]["intervention"] == {
            "catalog": "targets",
            "id": "survey",
            "revision": "2",
        }

        endpoint = f"/results/{ref}/location-inputs/linked"
        rows = []
        query = {"limit": 1}
        while True:
            response = await http.get(f"{endpoint}/rows", params=query)
            response.raise_for_status()
            page = response.json()
            wire = await call_tool(
                backend,
                "lyra_get_location_input_rows",
                {
                    "job_id": ref,
                    "input_name": "linked",
                    **query,
                },
            )
            assert wire["structuredContent"] == page
            rows.extend(page["rows"])
            if page["next_cursor"] is None:
                break
            query["cursor"] = page["next_cursor"]
        assert [
            (row["source_location_id"], row["location_id"], row["count"])
            for row in rows
        ] == [
            ("a", "a", "1"),
            ("x", "001", str(COUNT)),
            ("y", "001", str(COUNT)),
        ]
        assert shapely.from_wkt(rows[1]["geometry"]) == POINT
        response = await http.get(f"{endpoint}/download")
        response.raise_for_status()
        with pa.ipc.open_stream(response.content) as stream:
            for role in ("source_scope", "provider_scope"):
                assert json.loads(stream.schema.metadata[role.encode()]) == linked[role]
            check_pairs(gpd.GeoDataFrame.from_arrow(stream.read_all()))
