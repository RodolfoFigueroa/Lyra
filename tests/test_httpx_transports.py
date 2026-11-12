import asyncio
import io
import json
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import BinaryIO, TypeVar

import httpx
import pytest
from typing_extensions import Buffer, override

from lyra.api.client import endpoints
from lyra.api.client import httpx as client_httpx
from lyra.api.client.httpx import AsyncHTTPTransport, SyncHTTPTransport
from lyra.api.exceptions import DownloadError, JobNotReadyError, ServiceUnavailableError
from lyra.contracts.jobs import JobCreateResponse, TableJobResult
from tests.client_helpers import (
    _job_list_response,
    _job_response,
    _liveness_response,
    _redis_unavailable_response,
    _result_response,
    _status_response,
)

_T = TypeVar("_T")


class TransportHarness:
    def __init__(self, mode: str, runner: asyncio.Runner) -> None:
        self.mode = mode
        self.runner = runner
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response] = []
        self.failure: Exception | None = None
        self.clients: list[SyncHTTPTransport | AsyncHTTPTransport] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.responses.pop(0)

    def resolve(self, value: _T | Awaitable[_T]) -> _T:
        if isinstance(value, Awaitable):

            async def wait() -> _T:
                return await value

            return self.runner.run(wait())
        return value

    def open(
        self,
        *,
        timeout: float | httpx.Timeout | None = 30.0,
        headers: dict[str, str] | None = None,
        api_key: str | None = None,
        secure: bool = True,
    ) -> SyncHTTPTransport | AsyncHTTPTransport:
        cls = SyncHTTPTransport if self.mode == "sync" else AsyncHTTPTransport
        client = cls(
            "example.test/base/", timeout, headers, api_key=api_key, secure=secure
        )
        self.clients.append(client)
        return client

    def close(self, client: SyncHTTPTransport | AsyncHTTPTransport) -> None:
        if isinstance(client, SyncHTTPTransport):
            client.close()
        else:
            self.runner.run(client.aclose())


@pytest.fixture(params=["sync", "async"])
def transport(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TransportHarness]:
    with asyncio.Runner() as runner:
        harness = TransportHarness(request.param, runner)
        mock = httpx.MockTransport(harness.handle)
        monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=mock))
        monkeypatch.setattr(
            httpx, "AsyncClient", partial(httpx.AsyncClient, transport=mock)
        )
        yield harness
        for client in harness.clients:
            harness.close(client)


def test_requests_and_credentials(transport: TransportHarness) -> None:
    headers = {"X-Source": "test"}
    consumer = transport.open(headers=headers, api_key="consumer", secure=False)
    headers["X-Source"] = "mutated"
    admin = transport.open(api_key="admin")
    transport.responses = [
        httpx.Response(200, json=[]),
        httpx.Response(202, json=_job_response()),
        httpx.Response(200, json=_job_list_response()),
        httpx.Response(200, json=_result_response()),
    ]
    assert (
        transport.resolve(
            consumer.request(endpoints.get_metrics(query="a & b", offset=2, limit=3))
        )
        == []
    )
    result = transport.resolve(
        consumer.request(
            endpoints.create_job(
                "heavy_metric", inputs={"area": "x"}, parameters={"value": 3}
            )
        )
    )
    assert isinstance(result, JobCreateResponse)
    assert result.job_id == "job-1"
    jobs = transport.resolve(
        admin.request(
            endpoints.list_admin_jobs(
                status="running", queue="heavy", offset=2, limit=3
            )
        )
    )
    assert jobs.jobs[0].job_id == "job-1"
    table = transport.resolve(consumer.request(endpoints.get_job_result("job-1")))
    assert isinstance(table, TableJobResult)
    assert table.outputs == {"result": [{"value": 6}]}
    catalog, submission, listing, result_request = transport.requests
    assert catalog.url.scheme == "http"
    assert catalog.url.path == "/base/metrics"
    assert dict(catalog.url.params) == {"query": "a & b", "offset": "2", "limit": "3"}
    assert "authorization" not in catalog.headers
    assert catalog.headers["X-Source"] == "test"
    assert submission.method == "POST"
    assert submission.url.path == "/base/jobs"
    assert json.loads(submission.content) == {
        "metric": "heavy_metric",
        "inputs": {"area": "x"},
        "parameters": {"value": 3},
    }
    assert submission.headers["authorization"] == "Bearer consumer"
    assert listing.headers["authorization"] == "Bearer admin"
    assert listing.url.scheme == "https"
    assert listing.url.path == "/base/admin/jobs"
    assert dict(listing.url.params) == {
        "status": "running",
        "queue": "heavy",
        "offset": "2",
        "limit": "3",
    }
    assert result_request.url.path == "/base/jobs/job-1/result"


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [
        (30.0, dict.fromkeys(["connect", "read", "write", "pool"], 30.0)),
        (2.5, dict.fromkeys(["connect", "read", "write", "pool"], 2.5)),
        (None, dict.fromkeys(["connect", "read", "write", "pool"])),
        (
            httpx.Timeout(connect=1, read=2, write=3, pool=4),
            {"connect": 1, "read": 2, "write": 3, "pool": 4},
        ),
    ],
)
def test_timeouts_and_reuse(
    transport: TransportHarness,
    timeout: float | httpx.Timeout | None,
    expected: dict[str, float | None],
) -> None:
    client = transport.open(timeout=timeout)
    for _ in range(2):
        transport.responses.append(httpx.Response(200, json=_liveness_response()))
        assert (
            transport.resolve(client.request(endpoints.get_liveness())).status == "ok"
        )
    assert all(req.extensions["timeout"] == expected for req in transport.requests)
    transport.close(client)
    transport.close(client)
    with pytest.raises(RuntimeError, match="closed"):
        transport.resolve(client.request(endpoints.get_liveness()))
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"wrong": True}),
        httpx.Response(
            200, json=_result_response(), headers={"content-type": "text/plain"}
        ),
        httpx.Response(202, json={"status": "running"}),
        httpx.Response(202, json={**_status_response(), "status": "succeeded"}),
    ],
)
def test_invalid_responses_are_permanent(
    transport: TransportHarness, response: httpx.Response
) -> None:
    client = transport.open()
    transport.responses.append(
        httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )
    )
    with pytest.raises(DownloadError) as error:
        transport.resolve(client.request(endpoints.get_job_result("job-1")))
    assert not error.value.retryable
    assert len(transport.requests) == 1


def test_service_and_pending_errors(transport: TransportHarness) -> None:
    client = transport.open()
    transport.responses = [
        httpx.Response(
            503, json=_redis_unavailable_response(), headers={"Retry-After": "3"}
        ),
        httpx.Response(202, json=_status_response()),
        httpx.Response(202, text="broken"),
    ]
    with pytest.raises(
        ServiceUnavailableError, match="may have been accepted"
    ) as service:
        transport.resolve(client.request(endpoints.create_job("heavy_metric")))
    assert service.value.code == "redis_unavailable"
    assert service.value.retryable
    assert service.value.retry_after_seconds == 3
    with pytest.raises(JobNotReadyError) as pending:
        transport.resolve(client.request(endpoints.get_job_result("job-1")))
    assert pending.value.job_id == "job-1"
    assert pending.value.snapshot.status == "running"
    with pytest.raises(DownloadError, match="may have been accepted") as invalid:
        transport.resolve(client.request(endpoints.create_job("heavy_metric")))
    assert not invalid.value.retryable
    assert len(transport.requests) == 3


def _certificate_error() -> httpx.ConnectError:
    error = httpx.ConnectError("certificate verification failed")
    error.__cause__ = ssl.SSLCertVerificationError("untrusted certificate")
    return error


@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        (httpx.ConnectError("connection refused"), True),
        (httpx.ReadError("connection interrupted"), True),
        (httpx.WriteError("connection interrupted"), True),
        (httpx.ConnectTimeout("timeout"), True),
        (httpx.ReadTimeout("timeout"), True),
        (httpx.WriteTimeout("timeout"), True),
        (httpx.PoolTimeout("timeout"), True),
        (httpx.RemoteProtocolError("malformed HTTP response"), False),
        (httpx.LocalProtocolError("invalid request"), False),
        (httpx.UnsupportedProtocol("bad protocol"), False),
        (httpx.DecodingError("invalid encoding"), False),
        (httpx.InvalidURL("bad URL"), False),
        (_certificate_error(), False),
    ],
)
def test_transport_failure_chaining_without_retries(
    transport: TransportHarness, failure: Exception, *, retryable: bool
) -> None:
    client = transport.open()
    transport.failure = failure
    with pytest.raises(DownloadError, match="may have been accepted") as error:
        transport.resolve(client.request(endpoints.create_job("heavy_metric")))
    assert error.value.__cause__ is failure
    assert error.value.retryable is retryable
    assert len(transport.requests) == 1


def test_async_cancellation_and_cleanup_use_caller_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = []

    class CancelTransport(httpx.AsyncBaseTransport):
        @override
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/live"
            tasks.append(asyncio.current_task())
            raise asyncio.CancelledError

        @override
        async def aclose(self) -> None:
            tasks.append(asyncio.current_task())

    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=CancelTransport())
    )

    async def exercise() -> None:
        with pytest.raises(asyncio.CancelledError):
            async with AsyncHTTPTransport("example.test") as client:
                await client.request(endpoints.get_liveness())
        assert tasks == [asyncio.current_task(), asyncio.current_task()]
        with pytest.raises(RuntimeError, match="closed"):
            await client.request(endpoints.get_liveness())

    asyncio.run(exercise())


def test_context_closure(transport: TransportHarness) -> None:
    client = transport.open()
    transport.responses.append(httpx.Response(200, json=_liveness_response()))

    def sync_context(client: SyncHTTPTransport) -> None:
        with client:
            client.request(endpoints.get_liveness())

    async def async_context(client: AsyncHTTPTransport) -> None:
        async with client:
            await client.request(endpoints.get_liveness())

    def exercise() -> None:
        if isinstance(client, SyncHTTPTransport):
            sync_context(client)
        else:
            transport.runner.run(async_context(client))

    exercise()
    with pytest.raises(RuntimeError, match="closed"):
        exercise()
    with pytest.raises(RuntimeError, match="closed"):
        transport.resolve(client.request(endpoints.get_liveness()))


class ChunkStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        observe: Callable[[int], None] = lambda _: None,
        failure: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.observe = observe
        self.failure = failure
        self.closed = False

    @override
    def __iter__(self) -> Iterator[bytes]:
        consumed = 0
        for chunk in self.chunks:
            self.observe(consumed)
            yield chunk
            consumed += len(chunk)
        self.observe(consumed)
        if self.failure is not None:
            raise self.failure

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk

    @override
    def close(self) -> None:
        self.closed = True

    @override
    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.close()


@pytest.fixture
def destinations(monkeypatch: pytest.MonkeyPatch) -> list[BinaryIO]:
    files: list[BinaryIO] = []

    @contextmanager
    def open_file(path: str | os.PathLike[str]) -> Generator[BinaryIO]:
        with Path(path).open("wb") as file:
            files.append(file)
            yield file

    monkeypatch.setattr(client_httpx, "_open_destination", open_file)
    return files


def test_download_streams_before_consuming_next_chunk(
    transport: TransportHarness, tmp_path: Path, destinations: list[BinaryIO]
) -> None:
    client = transport.open(
        api_key="consumer",
        headers={"X-Source": "download"},
        timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4),
        secure=False,
    )
    path = tmp_path / "result.json"

    def observe(consumed: int) -> None:
        # Incremental consumption is a memory requirement: write each yielded
        # portion before asking the network for more, without buffering the body.
        assert destinations[0].tell() == consumed
        assert not destinations[0].closed

    stream = ChunkStream([b'{"result":', b"[1,2,3]}"], observe)
    response = httpx.Response(200, stream=stream)
    transport.responses.append(response)
    assert (
        transport.resolve(
            client.download("jobs/job-1/result", path, operation="download result")
        )
        is None
    )
    assert path.read_bytes() == b'{"result":[1,2,3]}'
    assert stream.closed
    assert response.is_closed
    assert destinations[0].closed
    request = transport.requests[0]
    assert str(request.url) == "http://example.test/base/jobs/job-1/result"
    assert request.headers["authorization"] == "Bearer consumer"
    assert request.headers["X-Source"] == "download"
    assert request.extensions["timeout"] == {
        "connect": 1,
        "read": 2,
        "write": 3,
        "pool": 4,
    }
    transport.responses.append(httpx.Response(200, json=_liveness_response()))
    assert transport.resolve(client.request(endpoints.get_liveness())).status == "ok"
    transport.close(client)
    with pytest.raises(RuntimeError, match="closed"):
        transport.resolve(
            client.download("jobs/job-1/result", path, operation="download")
        )


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize(
    "case",
    [
        (202, json.dumps(_status_response()), JobNotReadyError),
        (202, "invalid pending", DownloadError),
        (503, json.dumps(_redis_unavailable_response()), ServiceUnavailableError),
        (404, "missing", DownloadError),
        (302, "redirect", DownloadError),
        (500, "failed", DownloadError),
    ],
)
def test_download_rejected_before_opening_destination(
    transport: TransportHarness,
    tmp_path: Path,
    destinations: list[BinaryIO],
    case: tuple[int, str, type[Exception]],
    *,
    existing: bool,
) -> None:
    status, body, error_type = case
    path = tmp_path / "result.json"
    if existing:
        path.write_bytes(b"original")
    stream = ChunkStream([body.encode()])
    response = httpx.Response(status, stream=stream, headers={"Retry-After": "3"})
    transport.responses.append(response)
    with pytest.raises(error_type) as error:
        transport.resolve(
            transport.open().download("jobs/job-1/result", path, operation="download")
        )
    if isinstance(error.value, JobNotReadyError):
        assert error.value.job_id == "job-1"
        assert error.value.snapshot.status == "running"
    elif isinstance(error.value, ServiceUnavailableError):
        assert error.value.code == "redis_unavailable"
        assert error.value.retry_after_seconds == 3
    assert not destinations
    assert stream.closed
    assert response.is_closed
    assert path.read_bytes() == b"original" if existing else not path.exists()


@pytest.mark.parametrize("status", [200, 503])
def test_download_stream_failure_closes_resources(
    transport: TransportHarness,
    tmp_path: Path,
    destinations: list[BinaryIO],
    status: int,
) -> None:
    failure = httpx.ReadTimeout("stalled chunk")
    stream = ChunkStream([b"partial"], failure=failure)
    response = httpx.Response(status, stream=stream)
    transport.responses.append(response)
    with pytest.raises(DownloadError) as error:
        transport.resolve(
            transport.open().download("result", tmp_path / "out", operation="download")
        )
    assert error.value.__cause__ is failure
    assert error.value.retryable
    assert stream.closed
    assert response.is_closed
    assert all(file.closed for file in destinations)
    assert bool(destinations) == (status == 200)


def test_download_file_open_failure_closes_response(
    transport: TransportHarness,
    tmp_path: Path,
) -> None:
    stream = ChunkStream([b"data"])
    transport.responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(FileNotFoundError):
        transport.resolve(
            transport.open().download(
                "result", tmp_path / "missing" / "out", operation="download"
            )
        )
    assert stream.closed


def test_download_write_failure_closes_resources(
    transport: TransportHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("disk full")

    class BrokenFile(io.BytesIO):
        @override
        def write(self, buffer: Buffer, /) -> int:
            raise failure

    file = BrokenFile()
    monkeypatch.setattr(client_httpx, "_open_destination", lambda _: file)
    stream = ChunkStream([b"data"])
    transport.responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(OSError, match="disk full") as error:
        transport.resolve(
            transport.open().download("result", tmp_path / "out", operation="download")
        )
    assert error.value is failure
    assert stream.closed
    assert file.closed


def test_cancel_download_awaits_cleanup_in_caller_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    destinations: list[BinaryIO],
) -> None:
    async def exercise() -> None:
        streaming = asyncio.Event()
        closing = asyncio.Event()
        release = asyncio.Event()
        tasks = []

        class CancellableStream(httpx.AsyncByteStream):
            closed = False

            @override
            async def __aiter__(self) -> AsyncIterator[bytes]:
                tasks.append(asyncio.current_task())
                yield b"partial"
                streaming.set()
                await asyncio.Event().wait()

            @override
            async def aclose(self) -> None:
                tasks.append(asyncio.current_task())
                closing.set()
                await release.wait()
                await asyncio.sleep(0)
                self.closed = True

        stream = CancellableStream()
        response = httpx.Response(200, stream=stream)
        mock = httpx.MockTransport(lambda _: response)
        monkeypatch.setattr(
            httpx, "AsyncClient", partial(httpx.AsyncClient, transport=mock)
        )
        async with AsyncHTTPTransport("example.test") as client:
            task = asyncio.create_task(
                client.download("result", tmp_path / "out", operation="download")
            )
            await streaming.wait()
            task.cancel()
            await closing.wait()
            assert not task.done()
            assert destinations[0].closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
            assert response.is_closed
            assert tasks == [task, task]
        assert (tmp_path / "out").read_bytes() == b"partial"

    asyncio.run(exercise())
