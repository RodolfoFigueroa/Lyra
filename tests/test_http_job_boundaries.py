"""Authenticated HTTP job lookup boundaries use isolated native storage."""

import asyncio
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from starlette.types import Receive, Scope, Send

from lyra.api import AsyncLyraClient, LyraClient
from lyra.api.client import endpoints
from lyra.api.client.results import validate_download
from lyra.api.exceptions import DownloadError, ServiceUnavailableError
from lyra.contracts.errors import ResultIntegrityError
from lyra.contracts.inspection import TerminalResultOutput
from lyra.runtime.results import ResultStore
from lyra.server import job_store
from lyra.server.job_submission import job_links
from lyra.server.main import create_app
from lyra.server.mcp.models import GetJobResultInput, JobIdInput
from lyra.server.mcp.tools import InProcessLyraBackend, download_result, get_job_result
from tests.config_helpers import api_credentials, load_test_config
from tests.retained_job_helpers import retain
from tests.runtime_helpers import make_runtime

PATHS = [
    "/jobs/{job_id}",
    "/jobs/{job_id}/result",
    "/jobs/{job_id}/result/inspection",
    "/jobs/{job_id}/result/download",
    "/admin/jobs/{job_id}",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    config = load_test_config(tmp_path)
    application = create_app(config, credentials=api_credentials())
    application.state.runtime = make_runtime(config)
    return application


def headers(app: FastAPI, path: str) -> dict[str, str]:
    credentials = app.state.runtime.credentials
    key = (
        credentials.admin_api_key
        if path.startswith("/admin")
        else credentials.agent_api_key
    )
    return {"Authorization": f"Bearer {key}"}


def stored(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    status: JobStatus = JobStatus.FINISHED,
) -> SimpleNamespace:
    runtime = app.state.runtime
    job = retain(
        runtime.results.result_store_path,
        monkeypatch,
        {
            "kind": "table",
            "job_id": "job",
            "outputs": {"result": [{"value": 1}]},
            "output_metadata": {"result": {"columns": []}},
        },
        jobs=runtime.jobs,
        status=status,
    )
    job.origin, job.worker_name = "interactive", "worker"
    job.latest_result.return_value.exc_string = "diagnostics"
    return job


@pytest.mark.anyio
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "status",
    [JobStatus.QUEUED, JobStatus.STARTED, JobStatus.FINISHED, JobStatus.FAILED],
)
async def test_lifecycle(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str, status: JobStatus
) -> None:
    stored(app, monkeypatch, status)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            path.format(job_id="job"), headers=headers(app, path)
        )
    pending = status in {JobStatus.QUEUED, JobStatus.STARTED}
    assert response.status_code == (202 if pending and "/result" in path else 200)
    body = response.json()
    snapshot = body["snapshot"] if path.startswith("/admin") else body
    assert snapshot["job_id"] == "job"
    assert (
        snapshot["status"]
        == {
            JobStatus.QUEUED: "queued",
            JobStatus.STARTED: "running",
            JobStatus.FINISHED: "succeeded",
            JobStatus.FAILED: "failed",
        }[status]
    )
    if path.endswith("/download"):
        assert ("content-disposition" in response.headers) is not pending


def damage_storage(app: FastAPI, monkeypatch: pytest.MonkeyPatch, damage: str) -> None:
    job = stored(app, monkeypatch)
    if damage == "missing":
        monkeypatch.setattr(job_store.Job, "fetch", Mock(side_effect=NoSuchJobError))
    elif damage == "expired":
        app.state.runtime.jobs.connection.ttl.return_value = -2
    elif damage == "acceptance":
        job.args = ({"private": "secret"},)
    elif damage == "identity":
        job.args[0]["job_id"] = "another"
    elif damage == "table":
        with ResultStore.open(app.state.runtime.results.result_store_path) as reader:
            monkeypatch.setattr(
                reader, "read_result", Mock(side_effect=ResultIntegrityError("a" * 32))
            )
    else:
        job.latest_result.return_value = None


@pytest.mark.anyio
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "damage", ["missing", "expired", "acceptance", "identity", "artifact", "table"]
)
async def test_lookup_outcomes(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str, damage: str
) -> None:
    damage_storage(app, monkeypatch, damage)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            path.format(job_id="job"), headers=headers(app, path)
        )
    expected = 404 if damage in {"missing", "expired"} else 500
    if damage in {"artifact", "table"} and "/result" not in path:
        expected = 200  # Status and admin details do not require loading outputs.
    assert response.status_code == expected
    if expected == 200:
        return
    assert "secret" not in response.text
    if expected == 500:
        assert response.json()["detail"]["code"] == "invalid_job_result"
        assert response.json()["detail"]["retryable"] is False
    specs = {
        PATHS[0]: endpoints.get_job,
        PATHS[1]: endpoints.get_job_result,
        PATHS[2]: endpoints.get_result_inspection,
        PATHS[4]: endpoints.get_admin_job,
    }
    error_type = ServiceUnavailableError if expected == 500 else DownloadError
    if path.endswith("/download"):
        with pytest.raises(error_type) as error:
            validate_download(
                response.status_code, response.text, None, operation="download result"
            )
    else:
        with pytest.raises(error_type) as error:
            endpoints.validate_response(
                specs[path]("job"),
                response.status_code,
                response.text,
                "application/json",
                None,
            )
    assert error.value.retryable is False
    if isinstance(error.value, ServiceUnavailableError):
        assert error.value.code == "invalid_job_result"


@pytest.mark.anyio
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("job_id", [" ", " job ?#%+é ", "123", "null"])
async def test_encoded_identifier_is_preserved(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str, job_id: str
) -> None:
    fetch = Mock(side_effect=NoSuchJobError)
    monkeypatch.setattr(job_store.Job, "fetch", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            path.format(job_id=quote(job_id, safe="")), headers=headers(app, path)
        )
    assert response.status_code == 404
    assert fetch.call_args.args == (job_id,)


@pytest.mark.anyio
@pytest.mark.parametrize("path", PATHS)
async def test_authentication_precedes_lookup(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    fetch = Mock(side_effect=AssertionError("unauthorized lookup"))
    monkeypatch.setattr(job_store.Job, "fetch", fetch)
    opposite = "/jobs/job" if path.startswith("/admin") else "/admin/jobs/job"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get(path.format(job_id="job"))
        wrong = await client.get(
            path.format(job_id="job"), headers=headers(app, opposite)
        )
    assert missing.status_code in {401, 403}
    assert wrong.status_code == 403
    fetch.assert_not_called()


def test_openapi_job_inputs_and_outcomes(app: FastAPI) -> None:
    schema = app.openapi()
    for path in PATHS:
        operation = schema["paths"][path]["get"]
        parameter = next(p for p in operation["parameters"] if p["name"] == "job_id")
        assert parameter["required"] is True
        assert parameter["schema"]["type"] == "string"
        assert parameter["schema"]["minLength"] == 1
        assert {"200", "404", "422", "500"} <= operation["responses"].keys()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "suffix", ["", "/result", "/result/inspection", "/result/download"]
)
async def test_empty_path_segment_does_not_lookup(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    # Empty is the only invalid string; it cannot match a required path segment.
    fetch = Mock(side_effect=AssertionError("empty identifier lookup"))
    monkeypatch.setattr(job_store.Job, "fetch", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/jobs/{suffix}", headers=headers(app, "/jobs"))
    if suffix:
        assert response.status_code == 404
    else:
        # The collection route owns /jobs, so slash normalization redirects there.
        assert response.status_code == 307
        assert response.headers["location"] == "http://test/jobs"
    fetch.assert_not_called()


OPAQUE_IDS = [
    "a/b",
    "lyra://results/job",
    ".",
    "..",
    "a%2Fb",
    "λ/结果?#",
    " ",
    "a/result",
    "a/result/download",
    "/leading/",
    "a//b",
]


@pytest.mark.anyio
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("job_id", OPAQUE_IDS)
@pytest.mark.parametrize("outcome", ["retained", "missing", "damaged", "unauthorized"])
async def test_opaque_identifier_lookup(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    job_id: str,
    outcome: str,
) -> None:
    job = stored(app, monkeypatch)
    job.id = job_id
    job.args[0]["job_id"] = job_id
    fetch = Mock(return_value=job)
    if outcome == "missing":
        fetch.side_effect = NoSuchJobError
    elif outcome == "damaged":
        job.args = ()
    monkeypatch.setattr(job_store.Job, "fetch", fetch)
    auth = headers(app, path)
    if outcome == "unauthorized":
        auth = {"Authorization": "Bearer wrong"}
    component = endpoints.job_path(job_id).removeprefix("jobs/")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path.format(job_id=component), headers=auth)
    assert (
        response.status_code
        == {
            "retained": 200,
            "missing": 404,
            "damaged": 500,
            "unauthorized": 403,
        }[outcome]
    )
    if outcome == "unauthorized":
        fetch.assert_not_called()
    else:
        assert fetch.call_args.args == (job_id,)
    if outcome == "retained":
        body = response.json()
        assert (body["snapshot"] if path.startswith("/admin") else body)[
            "job_id"
        ] == job_id
    elif outcome == "damaged":
        assert response.json()["detail"]["code"] == "invalid_job_result"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["mount", "proxy", "stripped_proxy", "no_raw_path"])
@pytest.mark.parametrize("prefix", ["/api", "/área"])
async def test_job_routing_prefixes(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    prefix: str,
) -> None:
    job_id = "plain" if mode == "no_raw_path" else "a/result/download"
    job = stored(app, monkeypatch)
    job.id = job_id
    job.args[0]["job_id"] = job_id
    target = app
    if mode == "mount":
        target = FastAPI()
        target.mount(prefix, app)

    async def transport_app(scope: Scope, receive: Receive, send: Send) -> None:
        if mode == "no_raw_path":
            scope.pop("raw_path", None)
        await target(scope, receive, send)

    root_path = prefix if mode in {"proxy", "stripped_proxy"} else ""
    url_prefix = prefix if mode in {"mount", "proxy"} else ""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport_app, root_path=root_path),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"{url_prefix}/{endpoints.job_path(job_id)}", headers=headers(app, PATHS[0])
        )
        wrong_method = await client.post(
            f"{url_prefix}/{endpoints.job_path(job_id)}", headers=headers(app, PATHS[0])
        )
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert wrong_method.status_code == 405


@pytest.mark.anyio
@pytest.mark.parametrize("job_id", OPAQUE_IDS)
async def test_download_links_reach_retained_job(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
) -> None:
    job = stored(app, monkeypatch)
    job.id = job_id
    job.args[0]["job_id"] = job_id
    backend = InProcessLyraBackend(lambda: app.state.runtime)
    descriptor = await download_result(
        JobIdInput(job_id=job_id), backend, public_api_base_url="http://test/api"
    )
    inspection = await get_job_result(GetJobResultInput(job_id=job_id), backend)
    assert isinstance(inspection, TerminalResultOutput)
    links = job_links(job_id)
    mounted = FastAPI()
    mounted.mount("/api", app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mounted),
        base_url="http://test",
        headers=headers(app, PATHS[0]),
    ) as client:
        for url in [
            descriptor.lyra_api.url,
            f"/api{inspection.download}",
            f"/api{links.result}",
        ]:
            response = await client.get(url)
            assert response.status_code == 200
            assert response.json()["job_id"] == job_id
            assert response.json()["outputs"] == {"result": [{"value": 1}]}
        response = await client.get(f"/api{links.self}")
        assert response.json()["job_id"] == job_id


@pytest.mark.anyio
@pytest.mark.parametrize("job_id", ["a/result/download", ".", "..", "a%2Fb"])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_python_clients_reach_encoded_job(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
    *,
    asynchronous: bool,
) -> None:
    job = stored(app, monkeypatch)
    job.id = job_id
    job.args[0]["job_id"] = job_id
    native_sync, native_async = httpx.Client, httpx.AsyncClient
    async with native_async(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        loop = asyncio.get_running_loop()

        async def forward(request: httpx.Request) -> httpx.Response:
            return await http.send(request)

        def forward_sync(request: httpx.Request) -> httpx.Response:
            response = asyncio.run_coroutine_threadsafe(forward(request), loop).result(
                10
            )
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        monkeypatch.setattr(
            httpx,
            "Client",
            partial(native_sync, transport=httpx.MockTransport(forward_sync)),
        )
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(native_async, transport=httpx.MockTransport(forward)),
        )
        credential = app.state.runtime.credentials.agent_api_key

        def read_sync() -> None:
            with LyraClient("test", secure=False, agent_api_key=credential) as client:
                assert client.jobs.get(job_id).job_id == job_id
                assert client.results.get(job_id).job_id == job_id
                assert client.results.inspect(job_id).job_id == job_id

        if asynchronous:
            async with AsyncLyraClient(
                "test", secure=False, agent_api_key=credential
            ) as client:
                assert (await client.jobs.get(job_id)).job_id == job_id
                assert (await client.results.get(job_id)).job_id == job_id
                assert (await client.results.inspect(job_id)).job_id == job_id
        else:
            await asyncio.to_thread(read_sync)
