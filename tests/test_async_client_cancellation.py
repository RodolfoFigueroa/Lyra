"""Caller task identity and settled cleanup at public async client boundaries."""

from __future__ import annotations

import asyncio
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from typing_extensions import override

from lyra.api import AsyncLyraAdminClient, AsyncLyraClient
from tests.client_helpers import _job_response, _liveness_response, _status_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class CancellationHarness:
    def __init__(self, stage: str, *, cleanup_fails: bool) -> None:
        self.stage = stage
        self.cleanup_fails = cleanup_fails
        self.entered = asyncio.Event()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.tasks = []
        self.calls = []
        self.cleaned = False

    async def cleanup(self) -> None:
        self.tasks.append(asyncio.current_task())
        self.closing.set()
        await self.release.wait()
        self.cleaned = True
        if self.cleanup_fails:
            message = "cleanup failed"
            raise ValueError(message)

    async def pause(self, *_: object) -> None:
        self.tasks.append(asyncio.current_task())
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await self.cleanup()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json=_job_response())
        if request.url.path.endswith("/download"):
            return httpx.Response(200, stream=CancellableStream(self))
        if self.stage == "request":
            await self.pause()
        return httpx.Response(
            200,
            json=_status_response()
            | {
                "progress": {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "current": 1,
                    "stage": "work",
                }
            },
        )


class CancellableStream(httpx.AsyncByteStream):
    def __init__(self, harness: CancellationHarness) -> None:
        self.harness = harness

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.harness.tasks.append(asyncio.current_task())
        yield b"partial"
        self.harness.entered.set()
        await asyncio.Event().wait()

    @override
    async def aclose(self) -> None:
        await self.harness.cleanup()


@pytest.mark.parametrize("stage", ["request", "download", "callback", "sleep"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cancellation_settles_in_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str, *, cleanup_fails: bool
) -> None:
    async def exercise() -> None:
        harness = CancellationHarness(stage, cleanup_fails=cleanup_fails)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(harness.respond)),
        )
        if stage == "sleep":
            monkeypatch.setattr(
                "lyra.api.client.async_.asyncio", SimpleNamespace(sleep=harness.pause)
            )
        async with AsyncLyraClient("example.test") as client:
            handle = await client.jobs.submit("metric")
            if stage == "download":
                operation = client.results.download(handle.job_id, tmp_path / "result")
            else:
                operation = handle.wait(
                    on_progress=harness.pause if stage == "callback" else None
                )

            async def execute() -> None:
                await operation

            task = asyncio.create_task(execute())
            await harness.entered.wait()
            task.cancel()
            await harness.closing.wait()
            assert not task.done()
            harness.release.set()
            with pytest.raises(ValueError if cleanup_fails else asyncio.CancelledError):
                await task
            assert harness.cleaned
            assert harness.tasks == [task, task]
            assert all(
                method == "GET" or path == "/jobs" for method, path in harness.calls
            )
            assert len(harness.calls) == 2
        if stage == "download":
            assert (tmp_path / "result").read_bytes() == b"partial"

    asyncio.run(exercise())


@pytest.mark.parametrize("cls", [AsyncLyraClient, AsyncLyraAdminClient])
@pytest.mark.parametrize(
    "timeout", [30.0, None, httpx.Timeout(connect=1, read=2, write=3, pool=4)]
)
def test_native_async_lifecycle_and_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    cls: type[AsyncLyraClient | AsyncLyraAdminClient],
    timeout: float | httpx.Timeout | None,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == httpx.Timeout(timeout).as_dict()
        return httpx.Response(200, json=_liveness_response())

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
    )

    async def exercise() -> None:
        client = cls("example.test", timeout=timeout)
        try:
            results = await asyncio.gather(
                client.health.liveness(), client.health.liveness()
            )
            assert [result.status for result in results] == ["ok", "ok"]
            assert (await client.health.liveness()).status == "ok"
        finally:
            await client.aclose()
        await client.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await client.health.liveness()
        with pytest.raises(RuntimeError, match="closed"):
            async with client:
                pytest.fail("Closed client entered")

    asyncio.run(exercise())
