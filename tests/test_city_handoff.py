"""Run the documented city handoff through real interfaces and fake infrastructure."""

from __future__ import annotations

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
from lyra.sdk import GraphExecutionError, LocalRunner, plugin_loader
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.plugin_cli import render_manifest
from lyra.sdk.results import serialize_outputs
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from typing_extensions import override

from lyra_app import job_store, main, worker
from lyra_app.config import LyraConfig
from lyra_app.mcp.models import DownloadResultOutput, TerminalResultOutput
from tests.config_helpers import api_credentials
from tests.plugin_helpers import plugin_config
from tests.test_api_client_jobs import FakeAsyncResponse, FakeSession, FakeSyncResponse
from tests.test_docs_contract import (
    CONTENT_DIR,
    ROOT,
    city_tutorial_project,
    published_python,
)
from tests.test_mcp_server import (
    _ManagedTestClient,
    _mcp_headers,
    _tool_call_payload,
    _tool_payload,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import httpx
    from lyra.sdk.types import JsonObject


@dataclass
class CityServer:
    """Keep RQ's storage and queue boundaries in memory while executing real workers."""

    client: _ManagedTestClient
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
        assert function == "lyra_app.worker.run_metric_task"
        payload = json.loads(json.dumps(args[0]))
        self.submissions.append(JobEnvelope.model_validate(payload))
        now = datetime.now(UTC)
        native = SimpleNamespace(
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

    def sync_request(self, method: str, url: str, **kwargs: object) -> FakeSyncResponse:
        response = self.dispatch(method, url, **kwargs)
        return FakeSyncResponse(
            status_code=response.status_code,
            payload=response.json(),
            text=response.text,
            headers=dict(response.headers),
            chunks=[response.content],
        )

    def sync_get(self, url: str, **kwargs: object) -> FakeSyncResponse:
        return self.sync_request("GET", url, **kwargs)

    def tool(self, name: str, arguments: JsonObject) -> dict[str, Any]:
        return _tool_payload(
            self.client.post(
                "/mcp/",
                json=_tool_call_payload(name, arguments),
                headers={**_mcp_headers(), "Host": "localhost:5219"},
            )
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
        (tmp_path / "lyra.plugin.json").write_text(render_manifest(tmp_path))
        record = plugin_config(tmp_path)
        workspace.setattr(
            plugin_loader, "installed_manifest_path", lambda _: record.manifest_path
        )
        config = LyraConfig.model_validate(
            tomllib.loads((ROOT / "examples/lyra-deployment/lyra.toml").read_text())
        )
        config.workers["interactive"].temp_dir = tmp_path / "scratch"
        workspace.setattr(worker, "RUNNER_REGISTRY", {})
        workspace.setattr(worker, "_RUNNER_TEMP_BASE", None)
        workspace.setattr("lyra_app.worker._WORKER_CONFIG.config", None)
        worker.refresh_runner_registry("interactive", config=config)
        dummy_redis_clients[0].ttl.return_value = 60
        app = main.create_app(config, credentials=api_credentials())
        client = _ManagedTestClient(app)
        server = CityServer(client, config)
        workspace.setattr(job_store, "Queue", server.queue)
        workspace.setattr(worker, "get_current_job", lambda: server.native)
        workspace.setattr(job_store.Job, "fetch", server.fetch)
        workspace.setattr("lyra.api.client.sync.requests.request", server.sync_request)
        workspace.setattr("lyra.api.client.sync.requests.get", server.sync_get)

        class Session(FakeSession):
            @override
            def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
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
                return self.request("GET", *args, **kwargs)

        workspace.setattr("lyra.api.client.async_.aiohttp.ClientSession", Session)
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
        if "metric" in (payload := json.loads(source)) and "input" in payload
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
        inputs = cast("JsonObject", request["input"])
        expected = serialize_outputs(metric, local.run(metric, inputs))
        response = city_server.dispatch(
            "POST", "/jobs", json=request, headers=_mcp_headers()
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert (
            city_server.dispatch(
                "GET", f"/jobs/{job_id}", headers=_mcp_headers()
            ).json()["status"]
            == "succeeded"
        )
        result = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result", headers=_mcp_headers()
        )
        assert result.json()["outputs"] == expected
        downloaded = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result/download", headers=_mcp_headers()
        )
        assert downloaded.json() == result.json()
        descriptor = city_server.dispatch(
            "GET", f"/jobs/{job_id}/result/descriptor", headers=_mcp_headers()
        )
        assert descriptor.status_code == 200
        assert descriptor.json()["provenance"]["input"] == inputs
        assert_mcp_handoff(city_server, request, expected)
    assert len(city_server.submissions) == len(requests) * 2
    assert all(job.bound_plan is not None for job in city_server.submissions)


def assert_mcp_handoff(
    server: CityServer, request: JsonObject, expected: JsonObject
) -> None:
    inspection = server.tool("lyra_get_metric", {"metric": request["metric"]})
    assert set(inspection["outputs"]) == set(expected)
    submitted = server.tool("lyra_run_metric", request)
    assert submitted["next_tool"] == "lyra_get_job_result"
    reference = {"result_ref": submitted["result_ref"]}
    inspected = TerminalResultOutput.model_validate_json(
        json.dumps(server.tool("lyra_get_job_result", reference))
    )
    assert inspected.status == "succeeded"
    assert {
        name: output.preview for name, output in inspected.outputs.items()
    } == expected
    descriptor = server.dispatch(
        inspected.descriptor.method, inspected.descriptor.url, headers=_mcp_headers()
    )
    assert descriptor.json()["provenance"]["input"] == request["input"]
    link = DownloadResultOutput.model_validate(
        server.tool("lyra_download_result", reference)
    )
    complete = server.dispatch(
        link.lyra_api.method, link.lyra_api.url, headers=_mcp_headers()
    )
    assert complete.json()["outputs"] == expected


def test_city_validation_and_resolution_fail_at_different_stages(
    city_server: CityServer,
) -> None:
    request = published_requests("use/mcp.md")[0]
    inputs = cast("JsonObject", request["input"])
    parameters = cast("JsonObject", inputs["parameters"])
    parameters["condition_threshold"] = 101
    rejected = city_server.dispatch(
        "POST", "/jobs", json=request, headers=_mcp_headers()
    )
    assert rejected.status_code == 422
    rejected_tool = city_server.tool("lyra_run_metric", request)
    assert rejected_tool["error"]["code"] == "invalid_parameters"
    assert not city_server.submissions

    parameters["condition_threshold"] = 50
    streets = cast("JsonObject", inputs["streets"])
    streets["ids"] = ["missing-street"]
    accepted = city_server.dispatch(
        "POST", "/jobs", json=request, headers=_mcp_headers()
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    status = city_server.dispatch("GET", f"/jobs/{job_id}", headers=_mcp_headers())
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    assert status.json()["error"]["type"] == "location_resolution"
    reference: JsonObject = {"result_ref": f"lyra://results/{job_id}"}
    assert city_server.tool("lyra_get_job_result", reference)["status"] == "failed"
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
        headers=_mcp_headers(),
    ).json()
    assert result["outputs"]["maintenance"][0]["maintenance_length_m"] == pytest.approx(
        100.0
    )
    if filename == "run_remote.py":
        assert json.loads((tmp_path / "result.json").read_text()) == result
        assert namespace["metric"].accepted_selections["streets"]
