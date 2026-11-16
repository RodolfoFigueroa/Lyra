"""Validation and atomic file lifecycles at both public report resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import httpx
import pytest
from typing_extensions import override

from lyra.api import AsyncLyraClient, LyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import (
    InvalidExecutionIDError,
    ReportIntegrityError,
    ReportNotFoundError,
    ResultAccessError,
)
from lyra.contracts.reports import ExecutionReport
from lyra.contracts.retrieval_errors import error_payload, error_status
from tests.httpx_download_helpers import ChunkStream, destinations
from tests.report_access_helpers import Retained, retained
from tests.sqlite_helpers import database

if TYPE_CHECKING:
    from typing import IO

__all__ = ["database", "destinations", "retained"]
_T = TypeVar("_T")
_ID = "a" * 32


class ClientHarness:
    def __init__(self, mode: str, runner: asyncio.Runner) -> None:
        self.runner = runner
        self.responses: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []
        cls = LyraClient if mode == "sync" else AsyncLyraClient
        self.client = cls("example.test/api", agent_api_key="secret")

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer secret"
        assert request.url.path.startswith(f"/api/executions/{_ID}/report/")
        return self.responses.pop(0)

    def resolve(self, value: _T | Awaitable[_T]) -> _T:
        if isinstance(value, Awaitable):

            async def wait() -> _T:
                return await value

            return self.runner.run(wait())
        return value


@pytest.fixture(params=["sync", "async"])
def client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ClientHarness]:
    with asyncio.Runner() as runner:

        def respond(request: httpx.Request) -> httpx.Response:
            return harness.respond(request)

        mock = httpx.MockTransport(respond)
        monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=mock))
        monkeypatch.setattr(
            httpx, "AsyncClient", partial(httpx.AsyncClient, transport=mock)
        )
        harness = ClientHarness(request.param, runner)
        try:
            yield harness
        finally:
            if isinstance(harness.client, LyraClient):
                harness.client.close()
            else:
                runner.run(harness.client.aclose())


@pytest.mark.parametrize("method", ["get", "inspect", "download"])
@pytest.mark.parametrize(
    "error",
    [InvalidExecutionIDError(_ID), ReportNotFoundError(_ID), ReportIntegrityError(_ID)],
)
def test_report_errors(
    client: ClientHarness, tmp_path: Path, method: str, error: ResultAccessError
) -> None:
    payload = error_payload(error)
    client.responses.append(
        httpx.Response(error_status(payload), json={"detail": payload.model_dump()})
    )
    resource = client.client.reports
    with pytest.raises(type(error)) as caught:
        client.resolve(
            resource.download(_ID, tmp_path / "report.json")
            if method == "download"
            else resource.get(_ID)
            if method == "get"
            else resource.inspect(_ID)
        )
    assert vars(caught.value) == vars(error)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("method", ["get", "inspect"])
@pytest.mark.parametrize("failure", ["model", "json", "media_type"])
def test_report_validation(client: ClientHarness, method: str, failure: str) -> None:
    response = (
        httpx.Response(200, json={})
        if failure == "model"
        else httpx.Response(
            200, content="{", headers={"Content-Type": "application/json"}
        )
        if failure == "json"
        else httpx.Response(200, text="not JSON")
    )
    client.responses.append(response)
    with pytest.raises(DownloadError):
        client.resolve(getattr(client.client.reports, method)(_ID))


@pytest.mark.parametrize("existing", [False, True])
def test_report_download_is_complete_and_atomic(
    retained: Retained,
    client: ClientHarness,
    destinations: list[IO[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: bool,
) -> None:
    directory = retained.runtime.config.result_store_path.parent / "downloads"
    directory.mkdir()
    path = directory / "report.json"
    if existing:
        path.write_bytes(b"original")
    body = retained.report.model_dump_json().encode()

    def observe(consumed: int) -> None:
        # Bounded memory requires writing each chunk before reading the next one.
        assert destinations[0].tell() == consumed
        assert path.read_bytes() == b"original" if existing else not path.exists()

    stream = ChunkStream([body[:100], body[100:]], observe)
    response = httpx.Response(200, stream=stream)
    client.responses.append(response)
    replace = Path.replace

    def publish(source: Path, target: str | Path) -> Path:
        assert stream.closed
        assert response.is_closed
        assert destinations[0].closed
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", publish)
    assert client.resolve(client.client.reports.download(_ID, path)) is None
    assert ExecutionReport.model_validate_json(path.read_bytes()) == retained.report
    assert list(directory.iterdir()) == [path]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("stage", ["stream", "response_close", "replace", "interrupt"])
def test_report_download_failure_cleanup(
    client: ClientHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    *,
    existing: bool,
) -> None:
    path = tmp_path / "report.json"
    if existing:
        path.write_bytes(b"original")
    failure = OSError("download failed")

    class FailingStream(ChunkStream):
        @override
        def close(self) -> None:
            super().close()
            if stage == "response_close":
                raise failure

    def observe(consumed: int) -> None:
        if consumed and stage == "interrupt":
            # Sync interruption must settle the file, like async cancellation.
            raise KeyboardInterrupt

    stream = FailingStream(
        [b"partial"], observe, failure if stage == "stream" else None
    )
    response = httpx.Response(200, stream=stream)
    client.responses.append(response)
    if stage == "replace":

        def denied(_source: Path, _target: str | Path) -> Path:
            raise failure

        monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(KeyboardInterrupt if stage == "interrupt" else OSError):
        client.resolve(client.client.reports.download(_ID, path))
    assert stream.closed
    assert response.is_closed
    assert path.read_bytes() == b"original" if existing else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])


def test_report_download_missing_parent(client: ClientHarness, tmp_path: Path) -> None:
    stream = ChunkStream([b"body"])
    response = httpx.Response(200, stream=stream)
    client.responses.append(response)
    with pytest.raises(FileNotFoundError):
        client.resolve(
            client.client.reports.download(_ID, tmp_path / "absent" / "report.json")
        )
    assert stream.closed
    assert response.is_closed
    assert list(tmp_path.iterdir()) == []


def test_invalid_execution_id_precedes_network_and_files(
    client: ClientHarness, tmp_path: Path
) -> None:
    with pytest.raises(InvalidExecutionIDError):
        client.resolve(client.client.reports.download("bad", tmp_path / "report.json"))
    assert not client.requests
    assert not list(tmp_path.iterdir())
