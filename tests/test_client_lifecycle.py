"""Session ownership, cancellation, and deadline behavior without network access."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import CancelledError as FutureCancelledError
from typing import TYPE_CHECKING, ClassVar, Self

import aiohttp
import pytest

from lyra.api import (
    AsyncLyraAdminClient,
    AsyncLyraClient,
    DownloadError,
    JobWaitTimeoutError,
    LyraAdminClient,
    LyraClient,
)
from lyra.api.options import RunOptions
from tests.client_helpers import (
    FakeAsyncResponse,
    FakeSession,
    _job_response,
    _liveness_response,
    _result_response,
    _status_response,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


pytestmark = pytest.mark.usefixtures("sessions")


class Session(FakeSession):
    instances: ClassVar[list[Session]] = []

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.closed = False
        self.calls: list[str] = []
        self.options = kwargs
        self.close_count = 0
        self.instances.append(self)

    async def close(self) -> None:
        self.close_count += 1
        await super().close()

    def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
        assert kwargs["timeout"].ceil_threshold == float("inf")
        url = str(args[1])
        self.calls.append(url)
        if url.endswith("/live"):
            return FakeAsyncResponse(payload=_liveness_response())
        if args[0] == "POST":
            return FakeAsyncResponse(status=202, payload=_job_response())
        if url.endswith("/result"):
            return FakeAsyncResponse(payload=_result_response())
        return FakeAsyncResponse(payload={**_status_response(), "status": "succeeded"})

    def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert kwargs["headers"] == {"Authorization": "Bearer agent"}
        self.calls.append(str(args[0]))
        return FakeAsyncResponse(chunks=[b"result"])


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Session]]:
    Session.instances = []
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    yield Session.instances
    assert all(session.closed for session in Session.instances)


@pytest.mark.parametrize("cls", [LyraClient, LyraAdminClient])
def test_sync_lazy_context_and_idempotent_close(
    cls: type[LyraClient | LyraAdminClient], sessions: list[Session]
) -> None:
    client = cls("example.test")
    assert not sessions
    threads = set(threading.enumerate())
    with client:
        assert not sessions
        client.health.liveness()
        client.health.liveness()
        assert len(sessions) == 1
        assert isinstance(sessions[0].options["cookie_jar"], aiohttp.DummyCookieJar)
        owned = set(threading.enumerate()) - threads
        assert len(owned) == 1
    assert all(not thread.is_alive() for thread in owned)
    client.close()
    assert sessions[0].close_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        client.health.liveness()
    with pytest.raises(RuntimeError, match="closed"), client:
        pass


@pytest.mark.parametrize("cls", [AsyncLyraClient, AsyncLyraAdminClient])
def test_async_context_concurrent_use_and_close(
    cls: type[AsyncLyraClient | AsyncLyraAdminClient], sessions: list[Session]
) -> None:
    client = cls("example.test")
    assert not sessions

    async def run() -> None:
        async with client:
            assert not sessions
            results = await asyncio.gather(
                client.health.liveness(), client.health.liveness()
            )
            assert len(results) == 2
            assert len(sessions) == 1
        await client.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await client.health.liveness()
        async with client:
            pytest.fail("A closed client cannot be reentered")

    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(run())
    assert sessions[0].close_count == 1


def test_close_unused_clients_allocates_nothing(sessions: list[Session]) -> None:
    for cls in (LyraClient, LyraAdminClient):
        client = cls("example.test")
        client.close()
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            client.health.liveness()
    for cls in (AsyncLyraClient, AsyncLyraAdminClient):
        asynchronous = cls("example.test")
        asyncio.run(asynchronous.aclose())
        asyncio.run(asynchronous.aclose())
    assert not sessions


def test_async_loop_affinity(sessions: list[Session]) -> None:
    client = AsyncLyraClient("example.test")
    with asyncio.Runner() as first, asyncio.Runner() as second:
        first.run(client.health.liveness())
        with pytest.raises(RuntimeError, match="owning event loop"):
            second.run(client.health.liveness())
        with pytest.raises(RuntimeError, match="owning event loop"):
            second.run(client.aclose())
        first.run(client.aclose())
    assert len(sessions) == 1


def test_sync_session_spans_submission_wait_and_download(
    tmp_path: Path, sessions: list[Session]
) -> None:
    with LyraClient("example.test", agent_api_key="agent") as client:
        handle = client.raw.submit("metric", {})
        result = handle.wait()
        client.results.download(handle.job_id, tmp_path / "result")
        assert result.job_id == handle.job_id
        assert len(sessions) == 1
        assert len(sessions[0].calls) == 3
    assert (tmp_path / "result").read_bytes() == b"result"
    with pytest.raises(RuntimeError, match="closed"):
        handle.wait(timeout=0)


def test_async_session_spans_submission_wait_and_download(
    tmp_path: Path, sessions: list[Session]
) -> None:
    async def run() -> None:
        async with AsyncLyraClient("example.test", agent_api_key="agent") as client:
            handle = await client.raw.submit("metric", {})
            await handle.wait()
            await client.results.download(handle.job_id, tmp_path / "result")
            assert len(sessions) == 1
            assert len(sessions[0].calls) == 3
        with pytest.raises(RuntimeError, match="closed"):
            await handle.wait(timeout=0)

    asyncio.run(run())
    assert (tmp_path / "result").read_bytes() == b"result"


class StalledResponse(FakeAsyncResponse):
    def __init__(self, cleaned: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.cleaned = cleaned
        self.release = release

    async def text(self) -> str:
        assert not self.cleaned.is_set()
        await asyncio.Future()
        return "unreachable"

    async def __aexit__(self, *args: object) -> None:
        assert await asyncio.to_thread(self.release.wait, 2)
        self.cleaned.set()


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_wait_deadline_does_not_wait_for_cleanup(
    monkeypatch: pytest.MonkeyPatch, *, terminal: bool, asynchronous: bool
) -> None:
    cleaned, release = threading.Event(), threading.Event()
    original = Session.request

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        response = original(self, *args, **kwargs)
        if str(args[1]).endswith("/result" if terminal else "/jobs/job-1"):
            return StalledResponse(cleaned, release)
        return response

    monkeypatch.setattr(Session, "request", request)
    if asynchronous:

        async def run() -> None:
            async with AsyncLyraClient("example.test") as client:
                handle = await client.raw.submit("metric", {})
                started = time.monotonic()
                try:
                    with pytest.raises(JobWaitTimeoutError):
                        await client.raw.run(
                            "metric", {}, options=RunOptions(timeout=0.03)
                        ) if terminal else await handle.wait(timeout=0.03)
                    assert time.monotonic() - started < 0.5
                    assert not cleaned.is_set()
                    await client.health.liveness()
                finally:
                    release.set()

        asyncio.run(run())
    else:
        with LyraClient("example.test") as client:
            handle = client.raw.submit("metric", {})
            started = time.monotonic()
            try:
                with pytest.raises(JobWaitTimeoutError):
                    client.raw.run(
                        "metric", {}, options=RunOptions(timeout=0.03)
                    ) if terminal else handle.wait(timeout=0.03)
                assert time.monotonic() - started < 0.5
                assert not cleaned.is_set()
                client.health.liveness()
            finally:
                release.set()
    assert cleaned.is_set()


def test_async_cancellation_and_cancelled_close_keep_cleanup_owned(
    monkeypatch: pytest.MonkeyPatch, sessions: list[Session]
) -> None:
    entered = asyncio.Event()
    cleaned, release = threading.Event(), threading.Event()
    original = Session.request

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        original(self, *args, **kwargs)
        entered.set()
        return StalledResponse(cleaned, release)

    monkeypatch.setattr(Session, "request", request)

    async def run() -> None:
        client = AsyncLyraClient("example.test")
        task = asyncio.create_task(client.health.liveness())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not cleaned.is_set()
        with pytest.raises(RuntimeError, match="closed"):
            await client.health.liveness()
        release.set()
        await client.aclose()
        assert cleaned.is_set()
        assert sessions[0].closed

    asyncio.run(run())


def test_sync_callbacks_run_on_calling_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Session.request

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        response = original(self, *args, **kwargs)
        if str(args[1]).endswith("/jobs/job-1"):
            return FakeAsyncResponse(
                payload={
                    **_status_response(),
                    "status": "succeeded",
                    "progress": {
                        "current": 1,
                        "total": 1,
                        "message": "done",
                        "stage": "compute",
                        "timestamp": "2026-11-05T00:00:00Z",
                    },
                }
            )
        return response

    monkeypatch.setattr(Session, "request", request)
    threads = []
    with LyraClient("example.test") as client:
        handle = client.raw.submit("metric", {})
        handle.wait(on_progress=lambda _: threads.append(threading.get_ident()))
    assert threads == [threading.get_ident()]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_ordinary_request_has_total_budget(
    monkeypatch: pytest.MonkeyPatch, *, asynchronous: bool
) -> None:
    cleaned, release = threading.Event(), threading.Event()

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert self is Session.instances[0]
        assert args[0] == "GET"
        assert "timeout" in kwargs
        return StalledResponse(cleaned, release)

    monkeypatch.setattr(Session, "request", request)
    if asynchronous:

        async def run() -> None:
            async with AsyncLyraClient("example.test", timeout=0.03) as client:
                try:
                    with pytest.raises(DownloadError) as caught:
                        await client.health.liveness()
                    assert caught.value.retryable
                finally:
                    release.set()

        asyncio.run(run())
    else:
        with LyraClient("example.test", timeout=0.03) as client:
            try:
                with pytest.raises(DownloadError) as caught:
                    client.health.liveness()
                assert caught.value.retryable
            finally:
                release.set()
    assert cleaned.is_set()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_close_cancels_and_drains_active_requests(
    monkeypatch: pytest.MonkeyPatch, *, asynchronous: bool
) -> None:
    entered, cleaned, release = threading.Event(), threading.Event(), threading.Event()
    release.set()

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert self is Session.instances[0]
        assert args[0] == "GET"
        assert "timeout" in kwargs
        entered.set()
        return StalledResponse(cleaned, release)

    monkeypatch.setattr(Session, "request", request)
    if asynchronous:

        async def run() -> None:
            client = AsyncLyraClient("example.test")
            pending = asyncio.create_task(client.health.liveness())
            assert await asyncio.to_thread(entered.wait, 2)
            await client.aclose()
            with pytest.raises(asyncio.CancelledError):
                await pending

        asyncio.run(run())
    else:
        client = LyraClient("example.test")
        errors: list[BaseException] = []

        def observe() -> None:
            try:
                client.health.liveness()
            except FutureCancelledError as exc:
                errors.append(exc)

        caller = threading.Thread(target=observe)
        caller.start()
        try:
            assert entered.wait(2)
        finally:
            client.close()
            caller.join(timeout=2)
        assert not caller.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], FutureCancelledError)
    assert cleaned.is_set()


def test_late_cleanup_failure_is_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    cleaned, release = threading.Event(), threading.Event()
    original = Session.request

    class FailingCleanup(StalledResponse):
        async def __aexit__(self, *args: object) -> None:
            await super().__aexit__(*args)
            message = "cleanup failed"
            raise ValueError(message)

    def request(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        original(self, *args, **kwargs)
        return FailingCleanup(cleaned, release)

    monkeypatch.setattr(Session, "request", request)

    async def run() -> None:
        errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: errors.append(context)
        )
        async with AsyncLyraClient("example.test", timeout=0.03) as client:
            try:
                with pytest.raises(DownloadError):
                    await client.health.liveness()
            finally:
                release.set()
        assert cleaned.is_set()
        assert errors == []

    asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_run_to_file_downloads_once_after_completion(
    tmp_path: Path, sessions: list[Session], *, asynchronous: bool
) -> None:
    target = tmp_path / "result.json"
    if asynchronous:

        async def run() -> None:
            async with AsyncLyraClient("example.test", agent_api_key="agent") as client:
                await client.raw.run_to_file(
                    "metric", {}, target, options=RunOptions(timeout=1)
                )

        asyncio.run(run())
    else:
        with LyraClient("example.test", agent_api_key="agent") as client:
            client.raw.run_to_file("metric", {}, target, options=RunOptions(timeout=1))
    assert target.read_bytes() == b"result"
    assert sessions[0].calls == [
        "https://example.test/jobs",
        "https://example.test/jobs/job-1",
        "https://example.test/jobs/job-1/result/download",
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_run_to_file_deadline_covers_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, asynchronous: bool
) -> None:
    cleaned, release = threading.Event(), threading.Event()
    release.set()

    class StalledDownload(StalledResponse):
        async def __aenter__(self) -> Self:
            await asyncio.Future()
            return self

    def get(self: Session, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert self is Session.instances[0]
        assert str(args[0]).endswith("/result/download")
        budget = kwargs["timeout"]
        assert isinstance(budget, aiohttp.ClientTimeout)
        assert budget.total is not None
        assert 0 < budget.total <= 0.05
        return StalledDownload(cleaned, release)

    monkeypatch.setattr(Session, "get", get)
    target = tmp_path / "result.json"
    if asynchronous:

        async def run() -> None:
            async with AsyncLyraClient("example.test", agent_api_key="agent") as client:
                with pytest.raises(JobWaitTimeoutError):
                    await client.raw.run_to_file(
                        "metric", {}, target, options=RunOptions(timeout=0.05)
                    )
                await client.health.liveness()

        asyncio.run(run())
    else:
        with LyraClient("example.test", agent_api_key="agent") as client:
            with pytest.raises(JobWaitTimeoutError):
                client.raw.run_to_file(
                    "metric", {}, target, options=RunOptions(timeout=0.05)
                )
            client.health.liveness()
    assert not target.exists()
