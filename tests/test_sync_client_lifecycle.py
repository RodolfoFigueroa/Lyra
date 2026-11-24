"""Native synchronous clients own one pool and execute in the calling thread."""

from __future__ import annotations

import asyncio
import io
import threading
from functools import partial
from typing import TYPE_CHECKING

import httpx
import pytest

from lyra.api import DownloadError, LyraAdminClient, LyraClient, RunOptions
from lyra.api.client.httpx import SyncHTTPTransport
from tests.client_helpers import (
    _job_response,
    _liveness_response,
    _result_response,
    _status_response,
)
from tests.job_id_helpers import CLIENT_JOB_ID

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from pathlib import Path
    from typing import BinaryIO

    from typing_extensions import Buffer


class RecordingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.closed = 0
        self.thread = threading.get_ident()
        self.failure: httpx.RequestError | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert threading.get_ident() == self.thread
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        path = request.url.path
        if path.endswith("/download"):
            return httpx.Response(200, content=b"result")
        if path.endswith("/live"):
            return httpx.Response(200, json=_liveness_response())
        if request.method == "POST":
            return httpx.Response(202, json=_job_response())
        if path.endswith("/result"):
            return httpx.Response(200, json=_result_response())
        return httpx.Response(
            200,
            json={
                **_status_response("succeeded"),
                "progress": {
                    "current": 1,
                    "total": 1,
                    "message": "done",
                    "stage": "compute",
                    "timestamp": "2026-11-05T00:00:00Z",
                },
            },
        )

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingTransport]:
    transport = RecordingTransport()
    constructor = httpx.Client

    def client(*, timeout: float | httpx.Timeout | None) -> httpx.Client:
        return constructor(transport=transport, timeout=timeout)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Synchronous workflows must not dispatch to an event loop")

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(asyncio, "new_event_loop", forbidden)
    monkeypatch.setattr(asyncio, "run", forbidden)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", forbidden)
    yield transport
    assert transport.closed == 1


@pytest.mark.parametrize("cls", [LyraClient, LyraAdminClient])
@pytest.mark.parametrize(
    "timeout", [30.0, None, httpx.Timeout(connect=1, read=2, write=3, pool=4)]
)
def test_pool_reuse_timeout_and_closure(
    native: RecordingTransport,
    cls: type[LyraClient | LyraAdminClient],
    timeout: float | httpx.Timeout | None,
) -> None:
    client = cls(
        "example.test/base/",
        timeout=timeout,
        headers={"X-Source": "test"},
        secure=False,
    )
    with client:
        assert client.health.liveness().status == "ok"
        assert client.health.liveness().status == "ok"
        assert native.closed == 0
    assert len(native.calls) == 2
    assert all(
        call.extensions["timeout"] == httpx.Timeout(timeout).as_dict()
        for call in native.calls
    )
    assert str(native.calls[0].url) == "http://example.test/base/live"
    assert native.calls[0].headers["X-Source"] == "test"
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.health.liveness()
    with pytest.raises(RuntimeError, match="closed"), client:
        pass


def test_handle_workflow_and_file_writes_stay_in_caller(
    native: RecordingTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = []

    class ThreadFile(io.BufferedWriter):
        def write(self, buffer: Buffer, /) -> int:
            threads.append(threading.get_ident())
            return super().write(buffer)

    def destination(path: str | PathLike[str]) -> BinaryIO:
        return ThreadFile(io.FileIO(path, "w"))

    monkeypatch.setattr("lyra.api.client.httpx._open_destination", destination)
    target = tmp_path / "result"
    with LyraClient("example.test", agent_api_key="agent") as client:
        handle = client.jobs.submit("metric")
        handle.wait(on_progress=lambda _: threads.append(threading.get_ident()))
        assert handle.result().job_id == handle.job_id
        client.results.download(handle.job_id, target)
        client.jobs.run_to_file("metric", path=target, options=RunOptions(timeout=1))
        assert target.read_bytes() == b"result"
    assert threads == [threading.get_ident()] * 3
    assert all(call.headers["Authorization"] == "Bearer agent" for call in native.calls)
    assert [call.url.path for call in native.calls].count(
        f"/jobs/{CLIENT_JOB_ID}/result/download"
    ) == 2
    for operation in (handle.status, handle.result, handle.wait):
        with pytest.raises(RuntimeError, match="closed"):
            operation()


def test_explicit_close_and_request_failure(native: RecordingTransport) -> None:
    client = LyraClient("example.test")
    cause = httpx.ReadTimeout("read stalled")
    native.failure = cause
    try:
        with pytest.raises(DownloadError) as caught:
            client.jobs.create("metric")
        assert caught.value.__cause__ is cause
        assert caught.value.retryable
        assert "may have been accepted" in str(caught.value)
        assert len(native.calls) == 1
    finally:
        client.close()
        client.close()


def test_close_unused_client(native: RecordingTransport) -> None:
    client = LyraClient("example.test")
    client.close()
    client.close()
    assert not native.calls


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_download_interruption_preserves_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, existing: bool
) -> None:
    thread = threading.get_ident()
    path = tmp_path / "result"
    if existing:
        path.write_bytes(b"original")

    class InterruptedStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            assert threading.get_ident() == thread
            yield b"partial"
            assert path.read_bytes() == b"original" if existing else not path.exists()
            raise KeyboardInterrupt

        def close(self) -> None:
            assert threading.get_ident() == thread
            self.closed = True

    stream = InterruptedStream()
    response = httpx.Response(200, stream=stream)
    monkeypatch.setattr(
        httpx,
        "Client",
        partial(httpx.Client, transport=httpx.MockTransport(lambda _: response)),
    )
    with SyncHTTPTransport("example.test") as client, pytest.raises(KeyboardInterrupt):
        client.download("result", path, operation="download", atomic=True)
    assert stream.closed
    assert response.is_closed
    assert path.read_bytes() == b"original" if existing else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])
