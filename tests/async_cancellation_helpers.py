"""Controlled response streams for caller-owned cancellation tests."""

import asyncio
from collections.abc import AsyncIterator

import httpx
from typing_extensions import override

from tests.client_helpers import _job_response, _status_response


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
            return httpx.Response(
                200,
                stream=CancellableStream(self),
                headers={"Content-Type": "application/vnd.apache.arrow.stream"},
            )
        if self.stage in {"request", "rows"}:
            await self.pause()
        return httpx.Response(
            200,
            json=_status_response(
                "succeeded" if self.stage == "run_to_file" else "running"
            )
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
