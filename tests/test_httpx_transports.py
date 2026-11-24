import asyncio
import io
import json
import ssl
import tempfile
from collections.abc import AsyncIterator, Awaitable, Generator, Iterator
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import IO, TypeVar

import httpx
import pytest
from typing_extensions import Buffer, Unpack, override

from lyra.api.client import endpoints
from lyra.api.client import httpx as client_httpx
from lyra.api.client.httpx import AsyncHTTPTransport, SyncHTTPTransport
from lyra.api.exceptions import DownloadError, JobNotReadyError, ServiceUnavailableError
from lyra.contracts.errors import (
    ReportIntegrityError,
    ReportNotFoundError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.jobs import JobCreateResponse, TableJobResult
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.contracts.retrieval_paths import absolute_api_url, table_download_path
from tests.client_helpers import (
    _job_list_response,
    _job_response,
    _liveness_response,
    _redis_unavailable_response,
    _result_response,
    _status_response,
)
from tests.httpx_download_helpers import ChunkStream, TemporaryFileOptions, destinations
from tests.job_id_helpers import CLIENT_JOB_ID

__all__ = ["destinations"]

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
    assert result.job_id == CLIENT_JOB_ID
    jobs = transport.resolve(
        admin.request(endpoints.list_admin_jobs(status="running", offset=2, limit=3))
    )
    assert jobs.jobs[0].job_id == CLIENT_JOB_ID
    table = transport.resolve(consumer.request(endpoints.get_job_result(CLIENT_JOB_ID)))
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
        "offset": "2",
        "limit": "3",
    }
    assert result_request.url.path == f"/base/jobs/{CLIENT_JOB_ID}/result"


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
        httpx.Response(202, json=_status_response("succeeded")),
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
        transport.resolve(client.request(endpoints.get_job_result(CLIENT_JOB_ID)))
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
        transport.resolve(client.request(endpoints.get_job_result(CLIENT_JOB_ID)))
    assert pending.value.job_id == CLIENT_JOB_ID
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


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_download_streams_before_consuming_next_chunk(
    transport: TransportHarness,
    tmp_path: Path,
    destinations: list[IO[bytes]],
    *,
    atomic: bool,
    existing: bool,
) -> None:
    client = transport.open(
        api_key="consumer",
        headers={"X-Source": "download"},
        timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4),
        secure=False,
    )
    path = tmp_path / "result.json"
    if existing:
        path.write_bytes(b"original")

    def observe(consumed: int) -> None:
        # Incremental consumption is a memory requirement: write each yielded
        # portion before asking the network for more, without buffering the body.
        assert destinations[0].tell() == consumed
        assert not destinations[0].closed
        if atomic:
            assert path.read_bytes() == b"original" if existing else not path.exists()
            assert Path(destinations[0].name).parent == path.parent

    stream = ChunkStream([b'{"result":', b"[1,2,3]}"], observe)
    response = httpx.Response(200, stream=stream)
    transport.responses.append(response)
    assert (
        transport.resolve(
            client.download(
                f"jobs/{CLIENT_JOB_ID}/result",
                path,
                operation="download result",
                atomic=atomic,
            )
        )
        is None
    )
    assert path.read_bytes() == b'{"result":[1,2,3]}'
    assert stream.closed
    assert response.is_closed
    assert destinations[0].closed
    assert list(tmp_path.iterdir()) == [path]
    request = transport.requests[0]
    assert str(request.url) == f"http://example.test/base/jobs/{CLIENT_JOB_ID}/result"
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
            client.download(
                f"jobs/{CLIENT_JOB_ID}/result",
                path,
                operation="download",
            )
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
    destinations: list[IO[bytes]],
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
            transport.open().download(
                f"jobs/{CLIENT_JOB_ID}/result",
                path,
                operation="download",
            )
        )
    if isinstance(error.value, JobNotReadyError):
        assert error.value.job_id == CLIENT_JOB_ID
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
    destinations: list[IO[bytes]],
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


@pytest.mark.parametrize("atomic", [False, True])
def test_download_file_open_failure_closes_response(
    transport: TransportHarness,
    tmp_path: Path,
    *,
    atomic: bool,
) -> None:
    stream = ChunkStream([b"data"])
    transport.responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(FileNotFoundError):
        transport.resolve(
            transport.open().download(
                "result",
                tmp_path / "missing" / "out",
                operation="download",
                atomic=atomic,
            )
        )
    assert stream.closed
    assert not list(tmp_path.iterdir())


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
    destinations: list[IO[bytes]],
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


@pytest.mark.parametrize(
    "error",
    [
        ResultNotFoundError("a" * 32),
        ResultNotFoundError("a" * 32, "雪/a"),
        ResultIntegrityError("a" * 32),
        ResultIntegrityError("a" * 32, "雪/a"),
        ReportNotFoundError("b" * 32),
        ReportIntegrityError("b" * 32),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_retained_errors_in_json_and_downloads(
    transport: TransportHarness,
    tmp_path: Path,
    error: ResultAccessError,
    *,
    streaming: bool,
) -> None:
    payload = error_payload(error)
    raw = payload.model_dump(mode="json")
    raw["message"] = "Human wording does not determine exception category."
    response = httpx.Response(error_status(payload), json={"detail": raw})
    transport.responses.append(response)
    client = transport.open(api_key="consumer")
    destination = tmp_path / "existing.json"
    destination.write_bytes(b"original")
    operation = (
        partial(
            client.download,
            "results/reference",
            destination,
            operation="download",
            atomic=True,
        )
        if streaming
        else partial(client.request, endpoints.get_liveness())
    )
    with pytest.raises(type(error)) as captured:
        transport.resolve(operation())
    assert vars(captured.value) == vars(error)
    assert response.is_closed
    assert destination.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("streaming", [False, True])
def test_absolute_handoff_url_preserves_public_prefix_and_authentication(
    transport: TransportHarness,
    tmp_path: Path,
    *,
    streaming: bool,
) -> None:
    route = table_download_path("a" * 32, "雪 /%?#")
    link = absolute_api_url("https://public.example.test:8443/deployment/api/", route)
    transport.responses.append(httpx.Response(200, json=_liveness_response()))
    client = transport.open(api_key="consumer")
    if streaming:
        transport.resolve(
            client.download(link, tmp_path / "output", operation="download")
        )
    else:
        spec = replace(endpoints.get_liveness(), path=link, authenticated=True)
        transport.resolve(client.request(spec))
    request = transport.requests[0]
    assert str(request.url) == link
    assert request.headers["Authorization"] == "Bearer consumer"


@pytest.mark.parametrize("streaming", [False, True])
def test_unstructured_retained_failure_is_not_classified_by_message(
    transport: TransportHarness,
    tmp_path: Path,
    *,
    streaming: bool,
) -> None:
    transport.responses.append(httpx.Response(404, text="ResultNotFoundError"))
    client = transport.open()
    operation = (
        partial(client.download, "results/ref", tmp_path / "out", operation="download")
        if streaming
        else partial(client.request, endpoints.get_liveness())
    )
    with pytest.raises(DownloadError):
        transport.resolve(operation())


@pytest.mark.parametrize("streaming", [False, True])
def test_invalid_absolute_url_keeps_transport_error_policy(
    transport: TransportHarness,
    tmp_path: Path,
    *,
    streaming: bool,
) -> None:
    client = transport.open()
    route = "https://[invalid-ipv6]/download"
    operation = (
        partial(client.download, route, tmp_path / "out", operation="download")
        if streaming
        else partial(client.request, replace(endpoints.get_liveness(), path=route))
    )
    with pytest.raises(DownloadError) as captured:
        transport.resolve(operation())
    assert not captured.value.retryable
    assert isinstance(captured.value.__cause__, httpx.InvalidURL)


@pytest.fixture(params=[False, True])
def preserved_destination(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Generator[Path]:
    path = tmp_path / "result"
    existing = request.param
    if existing:
        path.write_bytes(b"original")
    yield path
    assert path.read_bytes() == b"original" if existing else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])


def _break_file_operation(
    file: IO[bytes], stage: str, failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    if stage == "write":
        write = file.write

        def partial_write(data: bytes) -> int:
            write(data[:2])
            raise failure

        monkeypatch.setattr(file, "write", partial_write)
    if stage == "file_close":
        close = file.close

        def failing_close() -> None:
            close()
            raise failure

        monkeypatch.setattr(file, "close", failing_close)


@pytest.mark.parametrize(
    "stage", ["stream", "write", "file_close", "response_close", "replace"]
)
def test_atomic_download_failure_preserves_destination(
    transport: TransportHarness,
    preserved_destination: Path,
    monkeypatch: pytest.MonkeyPatch,
    destinations: list[IO[bytes]],
    stage: str,
) -> None:
    path = preserved_destination
    failure = (
        httpx.ReadTimeout("stream stalled") if stage == "stream" else OSError(stage)
    )

    def observe(consumed: int) -> None:
        if consumed == 0:
            _break_file_operation(destinations[0], stage, failure, monkeypatch)

    class ClosingStream(ChunkStream):
        @override
        def close(self) -> None:
            super().close()
            if stage == "response_close":
                raise failure

    stream = ClosingStream(
        [b"partial", b"\x00\xffcomplete"],
        observe,
        failure if stage == "stream" else None,
    )
    response = httpx.Response(200, stream=stream)
    transport.responses.append(response)
    replace_path = Path.replace

    def replace(source: Path, target: str | Path) -> Path:
        # Publication requires both resource closures, even if replacement fails.
        assert stream.closed
        assert response.is_closed
        assert destinations[0].closed
        if stage == "replace":
            raise failure
        return replace_path(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(DownloadError if stage == "stream" else OSError) as caught:
        transport.resolve(
            transport.open().download("result", path, operation="download", atomic=True)
        )
    if stage == "stream":
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
    assert stream.closed
    assert response.is_closed
    assert destinations[0].closed


def test_atomic_publication_follows_resource_closure(
    transport: TransportHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destinations: list[IO[bytes]],
) -> None:
    path = tmp_path / "result"
    path.write_bytes(b"original")
    stream = ChunkStream([b"\x00\xff", b"body"])
    response = httpx.Response(200, stream=stream)
    transport.responses.append(response)
    replace_path = Path.replace

    def replace(source: Path, target: str | Path) -> Path:
        assert stream.closed
        assert response.is_closed
        assert destinations[0].closed
        assert path.read_bytes() == b"original"
        assert source.read_bytes() == b"\x00\xffbody"
        return replace_path(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    assert (
        transport.resolve(
            transport.open().download("result", path, operation="download", atomic=True)
        )
        is None
    )
    assert path.read_bytes() == b"\x00\xffbody"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("status", [202, 302, 404, 503])
def test_atomic_http_errors_do_not_create_files(
    transport: TransportHarness,
    tmp_path: Path,
    status: int,
    *,
    existing: bool,
) -> None:
    path = tmp_path / "result"
    if existing:
        path.write_bytes(b"original")
    stream = ChunkStream([json.dumps(_status_response()).encode()])
    response = httpx.Response(status, stream=stream)
    transport.responses.append(response)
    with pytest.raises(DownloadError):
        transport.resolve(
            transport.open().download("result", path, operation="download", atomic=True)
        )
    assert stream.closed
    assert response.is_closed
    assert path.read_bytes() == b"original" if existing else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])


def test_atomic_file_creation_failure_closes_response(
    transport: TransportHarness,
    preserved_destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = PermissionError("directory is read-only")

    def denied(**_options: Unpack[TemporaryFileOptions]) -> object:
        raise failure

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", denied)
    stream = ChunkStream([b"data"])
    response = httpx.Response(200, stream=stream)
    transport.responses.append(response)
    with pytest.raises(PermissionError) as caught:
        transport.resolve(
            transport.open().download(
                "result", preserved_destination, operation="download", atomic=True
            )
        )
    assert caught.value is failure
    assert stream.closed
    assert response.is_closed


def test_atomic_error_body_stream_failure_closes_response(
    transport: TransportHarness, preserved_destination: Path
) -> None:
    failure = httpx.ReadTimeout("error body stalled")
    stream = ChunkStream([b'{"detail":'], failure=failure)
    response = httpx.Response(503, stream=stream)
    transport.responses.append(response)
    with pytest.raises(DownloadError) as caught:
        transport.resolve(
            transport.open().download(
                "result", preserved_destination, operation="download", atomic=True
            )
        )
    assert caught.value.__cause__ is failure
    assert stream.closed
    assert response.is_closed


def test_row_request_encoding(transport: TransportHarness) -> None:
    consumer = transport.open(api_key="consumer")
    transport.responses.append(
        httpx.Response(200, json={"rows": [], "next_cursor": None})
    )
    page = transport.resolve(
        consumer.request(
            endpoints.get_result_rows("a" * 32, "named output", 3, "opaque+/=")
        )
    )
    assert page.rows == []
    request = transport.requests[-1]
    assert request.url.path.endswith("/tables/named output/rows")
    assert dict(request.url.params) == {"limit": "3", "cursor": "opaque+/="}
    assert request.headers["authorization"] == "Bearer consumer"
