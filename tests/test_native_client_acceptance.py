"""Submission helpers preserve their contract through native HTTPX serialization."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from lyra.api import AsyncLyraClient, DownloadError, LyraClient, RunOptions
from tests.client_helpers import (
    ClientRuntime,
    _job_response,
    _result_response,
    _status_response,
)

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


@pytest.mark.parametrize("method", ["create", "submit", "run", "run_to_file"])
@pytest.mark.parametrize(
    "sections",
    [
        {},
        {"inputs": None, "parameters": None},
        {"inputs": {}, "parameters": {}},
        {
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
    original = deepcopy(sections)
    result_payload = _result_response()

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            response = httpx.Response(202, json=_job_response())
        elif request.url.path.endswith(("/result", "/download")):
            response = httpx.Response(200, json=result_payload)
        else:
            response = httpx.Response(
                200, json=_status_response() | {"status": "succeeded"}
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
    options: dict[str, Any] = {"edition": "2025", "cache_revision": "revision"}
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
            assert outcome.job_id == "job-1"
        assert len(requests) == (3 if method in {"run", "run_to_file"} else 1)
        assert json.loads(requests[0].content) == {
            "metric": "metric",
            "inputs": sections.get("inputs") or {},
            "parameters": sections.get("parameters") or {},
            "edition": "2025",
            "cache_revision": "revision",
        }
        assert requests[0].url.path == "/base/jobs"
        assert sections == original
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
