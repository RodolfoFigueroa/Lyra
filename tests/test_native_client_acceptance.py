"""Submission helpers preserve their contract through native HTTPX serialization."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

import httpx
import pytest
from pydantic import ValidationError

from lyra.api import (
    AsyncJobHandle,
    AsyncLyraAdminClient,
    AsyncLyraClient,
    DownloadError,
    JobHandle,
    LyraAdminClient,
    LyraClient,
    RunOptions,
)
from lyra.data.interventions import Intervention
from tests.client_helpers import (
    ClientRuntime,
    _job_response,
    _result_response,
    _status_response,
)
from tests.job_id_helpers import CLIENT_JOB_ID, INVALID_IDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def native_factory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[
    [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
]:
    asynchronous = request.param
    native = httpx.AsyncClient if asynchronous else httpx.Client

    def install(
        respond: Callable[[httpx.Request], httpx.Response],
    ) -> type[LyraClient | AsyncLyraClient]:
        monkeypatch.setattr(
            httpx,
            "AsyncClient" if asynchronous else "Client",
            partial(native, transport=httpx.MockTransport(respond)),
        )
        return AsyncLyraClient if asynchronous else LyraClient

    return install


def _expected_body(sections: dict[str, Any]) -> dict:
    body = {
        "metric": "metric",
        "inputs": deepcopy(sections.get("inputs") or {}),
        "parameters": deepcopy(sections.get("parameters") or {}),
    }
    if sections.get("interventions"):
        body["interventions"] = [
            {
                "id": change.id,
                "catalog": change.catalog,
                "revision": change.revision,
                "add": [],
                "remove": list(change.remove),
            }
            for change in sections["interventions"]
        ]
    return body


@pytest.mark.parametrize("method", ["create", "submit", "run", "run_to_file"])
@pytest.mark.parametrize(
    "sections",
    [
        {},
        {"inputs": None, "parameters": None, "interventions": None},
        {"inputs": {}, "parameters": {}, "interventions": []},
        {
            "interventions": [
                Intervention(id="p", revision="v1", catalog="trees", remove=["01"])
            ],
            "inputs": {"places": {"ids": ["one"]}},
            "parameters": {"nested": [None, {"value": "área"}]},
        },
    ],
)
def test_native_submission_workflow(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    tmp_path: Path,
    method: str,
    sections: dict[str, Any],
) -> None:
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []
    original = _expected_body(sections)
    job_id = "a" * 32
    result_payload = _result_response() | {"job_id": job_id}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            response = httpx.Response(202, json=_job_response() | {"job_id": job_id})
        elif request.url.path.endswith(("/result", "/download")):
            response = httpx.Response(200, json=result_payload)
        else:
            response = httpx.Response(
                200, json=_status_response("succeeded") | {"job_id": job_id}
            )
        responses.append(response)
        return response

    cls = native_factory(respond)
    timeout = httpx.Timeout(connect=1, read=2, write=3, pool=4)
    client = cls(
        "example.test/base",
        agent_api_key="consumer",
        headers={"X-Source": "acceptance"},
        timeout=timeout,
    )
    target = tmp_path / "result.json"
    options: dict[str, Any] = {}
    if method in {"run", "run_to_file"}:
        options["options"] = RunOptions(timeout=10, poll_interval=0.1)
    if method == "run_to_file":
        options["path"] = target
    try:
        operation = getattr(client.jobs, method)("metric", **sections, **options)
        outcome = (
            client_runtime.run(operation)
            if inspect.isawaitable(operation)
            else operation
        )
        if method == "run_to_file":
            assert outcome is None
            assert json.loads(target.read_bytes()) == result_payload
        else:
            assert outcome.job_id == job_id
        assert len(requests) == (3 if method in {"run", "run_to_file"} else 1)
        assert json.loads(requests[0].content) == original
        if method == "submit":
            _observe_handle(outcome, client_runtime, job_id)
        _check_job_requests(requests[1:], job_id)
        assert requests[0].url.path == "/base/jobs"
        assert _expected_body(sections) == original
        assert all(response.is_closed for response in responses)
        for request in requests:
            assert request.headers["Authorization"] == "Bearer consumer"
            assert request.headers["X-Source"] == "acceptance"
            assert request.extensions["timeout"] == timeout.as_dict()
    finally:
        if isinstance(client, AsyncLyraClient):
            client_runtime.run(client.aclose())
        else:
            client.close()


@pytest.mark.parametrize("method", ["create", "submit", "run", "run_to_file"])
def test_native_submission_failure_is_never_retried(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    tmp_path: Path,
    method: str,
) -> None:
    requests: list[httpx.Request] = []
    cause = httpx.ReadTimeout("acceptance response lost")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise cause

    cls = native_factory(respond)
    client = cls("example.test")
    target = tmp_path / "result.json"
    options = {"path": target} if method == "run_to_file" else {}

    def execute() -> None:
        operation = getattr(client.jobs, method)("metric", **options)
        if inspect.isawaitable(operation):
            client_runtime.run(operation)

    try:
        with pytest.raises(DownloadError, match="may have been accepted") as error:
            execute()
        assert error.value.__cause__ is cause
        assert [(request.method, request.url.path) for request in requests] == [
            ("POST", "/jobs")
        ]
        assert not target.exists()
    finally:
        if isinstance(client, AsyncLyraClient):
            client_runtime.run(client.aclose())
        else:
            client.close()


@pytest.mark.parametrize(
    "operation",
    ["status", "get", "inspect", "tables", "download", "result_tables", "admin"],
)
@pytest.mark.parametrize(
    "job_id",
    [
        CLIENT_JOB_ID,
        "0123456789abcdef" * 2,
    ],
)
def test_native_job_identity(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    tmp_path: Path,
    operation: str,
    job_id: str,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"detail": "Job not found"})

    cls = native_factory(respond)
    client = cls("example.test/base")
    admin_cls = AsyncLyraAdminClient if cls is AsyncLyraClient else LyraAdminClient
    admin = admin_cls("example.test/base")
    path = tmp_path / "result"
    path.write_bytes(b"preserved")
    calls = {
        "status": lambda: client.jobs.get(job_id),
        "get": lambda: client.results.get(job_id=job_id),
        "inspect": lambda: client.results.inspect(job_id=job_id),
        "tables": lambda: client.results.tables(job_id=job_id),
        "download": lambda: client.results.download(job_id=job_id, path=path),
        "result_tables": lambda: client.result_tables(job_id=job_id),
        "admin": lambda: admin.jobs.get(job_id),
    }

    def execute() -> None:
        value = calls[operation]()
        if inspect.isawaitable(value):
            client_runtime.run(value)

    with pytest.raises(DownloadError, match="HTTP 404"):
        execute()
    assert len(requests) == 1
    url = requests[0].url
    prefix = b"/base/admin/jobs/" if operation == "admin" else b"/base/jobs/"
    suffix = {
        "get": b"/result",
        "inspect": b"/result/inspection",
        "download": b"/result/download",
    }.get(operation, b"")
    raw = url.raw_path
    assert raw.startswith(prefix)
    assert raw.endswith(suffix)
    component = raw[len(prefix) : len(raw) - len(suffix) if suffix else None]
    assert b"/" not in component
    assert unquote(component.decode()) == job_id
    assert not url.query
    assert not url.fragment
    assert path.read_bytes() == b"preserved"


@pytest.mark.parametrize("job_id", INVALID_IDS)
def test_native_invalid_ids_have_no_side_effects(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    tmp_path: Path,
    job_id: object,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    job_id = cast("str", job_id)
    cls = native_factory(respond)
    client = cls("example.test")
    admin_cls = AsyncLyraAdminClient if cls is AsyncLyraClient else LyraAdminClient
    admin = admin_cls("example.test")
    path = tmp_path / "existing"
    path.write_bytes(b"preserved")
    missing = tmp_path / "missing"
    calls = [
        lambda: client.jobs.get(job_id),
        lambda: client.results.get(job_id),
        lambda: client.results.inspect(job_id),
        lambda: client.results.tables(job_id),
        lambda: client.result_tables(job_id),
        lambda: client.results.download(job_id, path),
        lambda: client.results.download(job_id, missing),
        lambda: admin.jobs.get(job_id),
    ]

    def execute(call: Callable[[], object]) -> None:
        value = call()
        if inspect.isawaitable(value):
            client_runtime.run(value)

    for call in calls:
        with pytest.raises(ValidationError):
            execute(call)
    assert requests == []
    assert path.read_bytes() == b"preserved"
    assert not missing.exists()


def _observe_handle(
    handle: JobHandle | AsyncJobHandle, runtime: ClientRuntime, job_id: str
) -> None:
    for observe in (handle.status, handle.wait, handle.result):
        value = observe()
        observed = runtime.run(value) if inspect.isawaitable(value) else value
        assert observed.job_id == job_id


def _check_job_requests(requests: list[httpx.Request], job_id: str) -> None:
    for request in requests:
        component = request.url.raw_path.removeprefix(b"/base/jobs/").split(b"/")[0]
        assert unquote(component.decode()) == job_id
        assert not request.url.query
        assert not request.url.fragment


@pytest.mark.parametrize("section", ["inputs", "parameters"])
@pytest.mark.parametrize(
    "value", [(1, 2), {1, 2}, {1: "value"}, float("nan"), object()]
)
def test_non_json_submission_is_rejected_before_http(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    section: str,
    value: object,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    cls = native_factory(respond)
    client = cls("example.test")
    sections: dict[str, Any] = {section: {"nested": value}}

    def submit() -> None:
        operation = client.jobs.create("metric", **sections)
        if inspect.isawaitable(operation):
            client_runtime.run(operation)

    try:
        with pytest.raises(ValidationError):
            submit()
        assert requests == []
    finally:
        if isinstance(client, AsyncLyraClient):
            client_runtime.run(client.aclose())
        else:
            client.close()


@pytest.mark.parametrize("method", ["run", "run_to_file"])
def test_run_rejects_unknown_options_before_submission(
    native_factory: Callable[
        [Callable[[httpx.Request], httpx.Response]], type[LyraClient | AsyncLyraClient]
    ],
    client_runtime: ClientRuntime,
    tmp_path: Path,
    method: str,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = native_factory(respond)("https://example.test")
    options: dict[str, Any] = {"unexpected": True}
    if method == "run_to_file":
        options["path"] = tmp_path / "result.json"

    def invoke() -> None:
        result = getattr(client.jobs, method)("report", **options)
        if inspect.isawaitable(result):
            client_runtime.run(result)

    try:
        with pytest.raises(TypeError, match="Unsupported run options"):
            invoke()
        assert calls == []
    finally:
        if isinstance(client, AsyncLyraClient):
            client_runtime.run(client.aclose())
        else:
            client.close()
