"""Run the documented city handoff through real interfaces and fake infrastructure."""

from __future__ import annotations

import asyncio
import json
import re
import runpy
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock
from urllib.parse import urlencode, urlsplit

import pytest
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from typing_extensions import override

from docs.scripts.generate_docs import CONTENT_DIR
from lyra.api import AsyncLyraClient, LyraClient
from lyra.contracts.config import LyraConfig
from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobEnvelope
from lyra.data.results import serialize_outputs
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.server import job_store, main, worker
from lyra.server.mcp.models import DownloadResultOutput
from tests.client_helpers import (
    FakeAsyncResponse,
    FakeSession,
    FakeSyncResponse,
    install_sync_handler,
)
from tests.config_helpers import api_credentials
from tests.mcp_helpers import (
    ManagedTestClient,
    mcp_headers,
    tool_call_payload,
    tool_payload,
)
from tests.plugin_helpers import plugin_config
from tests.sqlite_helpers import database
from tests.tutorial_helpers import (
    ROOT,
    city_tutorial_project,
    published_python,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import httpx

    from lyra.contracts.types import JsonObject


__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@dataclass
class CityServer:
    """Keep RQ's storage and queue boundaries in memory while executing real workers."""

    client: ManagedTestClient
    config: LyraConfig
    native: SimpleNamespace | None = None
    jobs: dict[str, SimpleNamespace] = field(default_factory=dict)
    submissions: list[JobEnvelope] = field(default_factory=list)

    def queue(self, name: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(enqueue_call=partial(self.enqueue_call, name))

    def enqueue_call(
        self,
        queue: str,
        function: str,
        *,
        args: tuple[JsonObject],
        job_id: str,
        meta: JsonObject,
        **_options: object,
    ) -> None:
        assert function == "lyra.server.worker.run_metric_task"
        payload = json.loads(json.dumps(args[0]))
        self.submissions.append(JobEnvelope.model_validate(payload))
        now = datetime.now(UTC)
        native = SimpleNamespace(
            args=(payload,),
            id=job_id,
            key=f"rq:job:{job_id}",
            origin=queue,
            worker_name="city-test-worker",
            created_at=now,
            started_at=now,
            ended_at=now,
            meta=json.loads(json.dumps(meta)),
            save_meta=Mock(),
            get_status=Mock(return_value=JobStatus.FINISHED),
        )
        self.native = native
        self.jobs[native.id] = native
        try:
            result = worker.run_metric_task(payload)
            stored = json.loads(json.dumps(result))
            native.latest_result = Mock(
                return_value=SimpleNamespace(return_value=stored)
            )
        except GraphExecutionError:
            # RQ retains failed jobs after the worker records its failure metadata.
            native.get_status.return_value = JobStatus.FAILED
        finally:
            self.native = None

    def fetch(self, job_id: str, **_kwargs: object) -> SimpleNamespace:
        if job_id not in self.jobs:
            raise NoSuchJobError(job_id)
        return self.jobs[job_id]

    def dispatch(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        parsed = urlsplit(url)
        path = parsed.path
        query = urlencode(cast("dict[str, object]", kwargs.get("params") or {}))
        query = query or parsed.query
        if query:
            path += f"?{query}"
        headers = dict(cast("dict[str, str]", kwargs.get("headers") or {}))
        headers["Host"] = "localhost:5219"
        if method == "GET":
            return self.client.get(path, headers=headers)
        assert method == "POST"
        return self.client.post(
            path, headers=headers, json=cast("JsonObject", kwargs["json"])
        )

    def tool(self, name: str, arguments: JsonObject) -> dict[str, Any]:
        return tool_payload(
            self.client.post(
                "/mcp/",
                json=tool_call_payload(name, arguments),
                headers={**mcp_headers(), "Host": "localhost:5219"},
            ),
            wrapped=name in {"lyra_inspect_report", "lyra_describe_result"},
        )


@pytest.fixture
def city_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dummy_redis_clients: tuple[Mock, Mock],
) -> Iterator[CityServer]:
    """Load the authored capstone with the actual deployment plugin configuration.

    Yields:
        An in-process API, worker, and stored job results.
    """
    with city_tutorial_project(tmp_path, monkeypatch) as (_sources, workspace):
        plugin_config(tmp_path)
        config = LyraConfig.model_validate(
            tomllib.loads((ROOT / "examples/lyra-deployment/lyra.toml").read_text())
        )
        config.result_store_path = tmp_path / "results.sqlite3"
        config.result_store_path.touch()
        config.workers["interactive"].temp_dir = tmp_path / "scratch"
        workspace.setattr(worker, "_WORKER_RUNTIME", None)
        workspace.setattr(worker, "_RUNNER_TEMP_BASE", None)
        worker.initialize_worker("interactive", config=config)
        dummy_redis_clients[0].ttl.return_value = 60
        app = main.create_app(config, credentials=api_credentials())
        client = ManagedTestClient(app)
        server = CityServer(client, config)
        workspace.setattr(job_store, "Queue", server.queue)
        workspace.setattr(worker, "get_current_job", lambda: server.native)
        workspace.setattr(job_store.Job, "fetch", server.fetch)

        class Session(FakeSession):
            @override
            def dispatch(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
                response = server.dispatch(str(args[0]), str(args[1]), **kwargs)
                return FakeAsyncResponse(
                    status=response.status_code,
                    payload=response.json(),
                    text=response.text,
                    headers=dict(response.headers),
                    chunks=[response.content],
                )

            @override
            def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
                return self.dispatch("GET", *args, **kwargs)

        workspace.setattr("httpx.AsyncClient", Session)

        def sync_request(method: str, url: str, **kwargs: object) -> FakeSyncResponse:
            response = server.dispatch(method, url, **kwargs)
            return FakeSyncResponse(
                status_code=response.status_code,
                text=response.text,
                headers=dict(response.headers),
                chunks=[response.content],
            )

        install_sync_handler(workspace, sync_request)
        try:
            yield server
        finally:
            client.close()


def published_requests(relative: str) -> list[JsonObject]:
    """Read executable JSON requests without making prose part of the contract."""
    content = (CONTENT_DIR / relative).read_text()
    sources = re.findall(r"```json[^\n]*\n(.*?)\n```", content, re.DOTALL)
    sources += re.findall(r"-d '(.*?)'", content, re.DOTALL)
    return [
        payload
        for source in sources
        if "metric" in (payload := json.loads(source)) and "inputs" in payload
    ]


@pytest.mark.parametrize(
    "guide",
    ["quickstart.mdx", "use/rest-api.md", "use/mcp.md", "plugins/tutorial/package.md"],
)
def test_documented_requests_survive_server_handoff(
    city_server: CityServer, guide: str
) -> None:
    requests = published_requests(guide)
    assert requests
    factory = import_module("city_example").create_plugin
    assert factory.__module__ == "city_example.tutorial"
    local = LocalRunner(
        plugins=[
            item.distribution
            for item in city_server.config.plugins.installed
            if item.enabled
        ]
    )
    for request in requests:
        metric = cast("str", request["metric"])
        inputs = cast("JsonObject", request["inputs"])
        expected = serialize_outputs(
            metric,
            local.run(
                metric,
                inputs=inputs,
                parameters=cast("JsonObject | None", request.get("parameters")),
            ).tables,
        )
        response = city_server.dispatch(
            "POST", "/jobs", json=request, headers=mcp_headers()
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert (
            city_server.dispatch(
                "GET", f"/jobs/{job_id}", headers=mcp_headers()
            ).json()["status"]
            == "succeeded"
        )
        result = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result", headers=mcp_headers()
        )
        assert result.json()["outputs"] == expected
        downloaded = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result/download", headers=mcp_headers()
        )
        assert downloaded.json() == result.json()
        descriptor = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result/inspection", headers=mcp_headers()
        )
        assert descriptor.status_code == 200
        assert result.json()["provenance"]["accepted"]["plan"]["request"][
            "parameters"
        ] == request.get("parameters", {})
        assert result.json()["provenance"]["accepted"]["plan"]["request"]["inputs"] == {
            name: value for name, value in inputs.items() if name != "parameters"
        }
        assert_mcp_handoff(city_server, request, expected)
    assert len(city_server.submissions) == len(requests) * 2
    assert all(job.accepted.plan is not None for job in city_server.submissions)


def assert_mcp_handoff(
    server: CityServer, request: JsonObject, expected: JsonObject
) -> None:
    inspection = server.tool("lyra_get_metric", {"metric": request["metric"]})
    assert set(inspection["outputs"]) == set(expected)
    submitted = server.tool("lyra_run_metric", request)
    assert submitted["next_tool"] == "lyra_get_job_result"
    reference = {"job_id": submitted["job_id"]}
    inspected = TerminalResultOutput.model_validate_json(
        json.dumps(server.tool("lyra_get_job_result", reference)["result"])
    )
    assert inspected.status == "succeeded"
    assert {
        name: output.preview for name, output in inspected.outputs.items()
    } == expected
    result = server.dispatch("GET", inspected.download, headers=mcp_headers())
    assert result.json()["provenance"]["accepted"]["plan"]["request"]["inputs"] == {
        name: value
        for name, value in cast("dict", request["inputs"]).items()
        if name != "parameters"
    }
    link = DownloadResultOutput.model_validate(
        server.tool("lyra_download_result", reference)
    )
    complete = server.dispatch(
        link.lyra_api.method, link.lyra_api.url, headers=mcp_headers()
    )
    assert complete.json()["outputs"] == expected


def discover_street_schema(server: CityServer, name: str, proposal: dict) -> dict:
    metric = server.tool("lyra_get_metric", {"metric": name})
    provider = metric["providers"][proposal["catalog"]]
    assert provider["membership_capable"]
    schema = provider["record_schema"]
    columns = {column["name"]: column for column in schema["columns"]}
    (addition,) = proposal["add"]
    assert set(addition) == columns.keys()
    assert columns[schema["geometry"]]["parameters"]["crs"] == "EPSG:32614"
    assert columns["condition_score"]["unit"] == "dimensionless"
    return schema


def test_inline_street_proposal_through_json_tools(city_server: CityServer) -> None:
    request = next(
        body
        for body in published_requests("reference/mcp-payloads.md")
        if "interventions" in body
    )
    (proposal,) = cast("list[dict]", request["interventions"])
    schema = discover_street_schema(
        city_server, cast("str", request["metric"]), proposal
    )
    tool = city_server.tool

    submitted = tool("lyra_run_metric", request)
    job = tool("lyra_get_job_result", {"job_id": submitted["job_id"]})["result"]
    assert job["status"] == "succeeded"
    city_server.jobs.clear()  # Expire the queue entry before durable reads.
    ref = job["job_id"]
    report = tool("lyra_inspect_report", {"job_id": job["job_id"]})["result"]
    assert report["kind"] == "complete"
    (accepted,) = report["report"]["request"]["interventions"]
    assert {key: accepted[key] for key in proposal} == proposal
    assert accepted["addition_schema"] == schema
    (validation,) = report["report"]["intervention_validations"]
    assert validation["membership_lookup"] == "executed"
    scope = validation["scope"]
    assert scope["intervention"] == {
        key: proposal[key] for key in ("catalog", "id", "revision")
    }
    description = tool("lyra_describe_result", {"job_id": ref})["result"]["description"]
    assert description["location_inputs"]["streets"]["provider_scope"] == scope
    assert tool(
        "lyra_get_location_input_rows",
        {
            "job_id": ref,
            "input_name": "streets",
            "limit": 1,
        },
    ) == {"rows": proposal["add"], "next_cursor": None}
    expected = [{"street_id": "proposed-1", "length_m": 150.0}]
    assert tool(
        "lyra_get_result_rows",
        {
            "job_id": ref,
            "output_name": "lengths",
            "limit": 1,
        },
    ) == {"rows": expected, "next_cursor": None}

    response = city_server.dispatch(
        "POST", "/jobs", json=request, headers=mcp_headers()
    )
    assert response.status_code == 202
    repeated = tool("lyra_get_job_result", {"job_id": response.json()["job_id"]})[
        "result"
    ]
    assert repeated["outputs"]["lengths"]["preview"] == expected


@pytest.mark.usefixtures("city_server")
def test_native_street_proposal_across_runners_and_clients(tmp_path: Path) -> None:
    path = tmp_path / "street_proposal.py"
    path.write_text(published_python("use/local-execution.md", path.name))
    example = runpy.run_path(str(path))
    metric = "city_street_lengths"
    inputs, changes = example["inputs"], [example["proposal"]]
    expected = [{"street_id": "proposed-1", "length_m": 150.0}]
    assert example["result"].tables["lengths"].to_dict("records") == expected
    development = example["runner"].run_development(
        metric, inputs=inputs, interventions=changes
    )
    assert development.tables["lengths"].to_dict("records") == expected

    async def async_outputs() -> dict:
        async with AsyncLyraClient(
            "example.test", agent_api_key="agent-secret"
        ) as client:
            return (
                await client.jobs.run(metric, inputs=inputs, interventions=changes)
            ).outputs

    with LyraClient("example.test", agent_api_key="agent-secret") as client:
        assert client.jobs.run(
            metric, inputs=inputs, interventions=changes
        ).outputs == {"lengths": expected}
    assert asyncio.run(async_outputs()) == {"lengths": expected}


def test_retained_city_table_matches_local_run_beyond_preview(
    city_server: CityServer, tmp_path: Path
) -> None:
    source = tmp_path / "city_example/data/city.json"
    city = json.loads(source.read_text())
    # Expand the temporary tutorial dataset past the five-row inspection limit.
    city["streets"] += [
        {**street, "id": street["id"] + "-copy"} for street in city["streets"]
    ]
    source.write_text(json.dumps(city))
    inputs: JsonObject = {
        "streets": {
            "provider": "city_streets",
            "ids": [street["id"] for street in city["streets"]],
        }
    }
    metric = "city_street_maintenance"
    local = LocalRunner(plugins=["lyra-city-example"])
    expected = (
        local.run(
            metric,
            inputs={k: v for k, v in inputs.items() if k != "parameters"},
            parameters=cast("JsonObject | None", inputs.get("parameters")),
        )
        .tables["maintenance"]
        .to_dict("records")
    )
    assert len(expected) > 5

    with LyraClient("example.test", agent_api_key="agent-secret") as client:
        job = client.jobs.submit(
            metric,
            inputs={k: v for k, v in inputs.items() if k != "parameters"},
            parameters=cast("JsonObject", inputs.get("parameters", {})),
        )
        job.wait()
        retained = city_server.jobs[job.job_id].latest_result().return_value
        assert set(retained) == {"job_id"}
        inspection = client.results.inspect(job.job_id)
        assert isinstance(inspection, TerminalResultOutput)
        assert inspection.job_id == retained["job_id"]
        output = inspection.outputs["maintenance"]
        assert output.preview == expected[:5]
        assert output.truncation.omitted_rows == len(expected) - 5
        table = client.result_tables(job.job_id)["maintenance"]
        assert table.to_dict("records") == expected


def test_city_validation_and_resolution_fail_at_different_stages(
    city_server: CityServer,
) -> None:
    request = published_requests("use/mcp.md")[0]
    inputs = cast("JsonObject", request["inputs"])
    parameters = cast("JsonObject", request["parameters"])
    parameters["condition_threshold"] = 101
    rejected = city_server.dispatch(
        "POST", "/jobs", json=request, headers=mcp_headers()
    )
    assert rejected.status_code == 422
    rejected_tool = city_server.tool("lyra_run_metric", request)
    assert rejected_tool["error"]["code"] == "invalid_parameters"
    assert not city_server.submissions

    parameters["condition_threshold"] = 50
    streets = cast("JsonObject", inputs["streets"])
    streets["ids"] = ["missing-street"]
    accepted = city_server.dispatch(
        "POST", "/jobs", json=request, headers=mcp_headers()
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    status = city_server.dispatch("GET", f"/jobs/{job_id}", headers=mcp_headers())
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    assert status.json()["error"]["type"] == "location_resolution"
    reference: JsonObject = {"job_id": job_id}
    assert (
        city_server.tool("lyra_get_job_result", reference)["result"]["status"]
        == "failed"
    )
    assert (
        city_server.tool("lyra_download_result", reference)["error"]["code"]
        == "result_not_downloadable"
    )


@pytest.mark.parametrize("filename", ["run_remote.py", "run_remote_async.py"])
def test_documented_python_client_runs_against_city_server(
    city_server: CityServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    monkeypatch.setenv("LYRA_AGENT_API_KEY", "agent-secret")
    monkeypatch.chdir(tmp_path)
    path = tmp_path / filename
    path.write_text(published_python("use/python-client.md", filename))
    namespace = runpy.run_path(str(path), run_name="__main__")
    assert len(city_server.submissions) == 1
    result = city_server.dispatch(
        "GET",
        f"/jobs/{city_server.submissions[0].job_id}/result",
        headers=mcp_headers(),
    ).json()
    assert result["outputs"]["maintenance"][0]["maintenance_length_m"] == pytest.approx(
        100.0
    )
    if filename == "run_remote.py":
        assert json.loads((tmp_path / "result.json").read_text()) == result
        assert namespace["metric"].metric.accepted_selections["streets"]
