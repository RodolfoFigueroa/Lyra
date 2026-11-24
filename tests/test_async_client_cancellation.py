"""Caller task identity and settled cleanup at public async client boundaries."""

from __future__ import annotations

import asyncio
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from lyra.api import AsyncLyraAdminClient, AsyncLyraClient
from lyra.api.client.httpx import AsyncHTTPTransport
from tests.async_cancellation_helpers import CancellationHarness
from tests.client_helpers import _liveness_response

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "stage", ["request", "describe", "download", "run_to_file", "callback", "sleep"]
)
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
            if stage == "run_to_file":
                operation = client.jobs.run_to_file("metric", path=tmp_path / "result")
            else:
                handle = await client.jobs.submit("metric")
                if stage == "describe":
                    operation = client.results.describe("a" * 32)
                elif stage == "download":
                    operation = client.results.download(
                        handle.job_id, tmp_path / "result"
                    )
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
            assert len(harness.calls) == (3 if stage == "run_to_file" else 2)
        if stage in {"download", "run_to_file"}:
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


@pytest.mark.parametrize("boundary", ["transport", "reports", "tables", "inputs"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_atomic_download_cancellation_preserves_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    existing: bool,
    cleanup_fails: bool,
    boundary: str,
) -> None:
    path = tmp_path / "result"
    if existing:
        path.write_bytes(b"original")

    async def exercise() -> None:
        harness = CancellationHarness("download", cleanup_fails=cleanup_fails)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(harness.respond)),
        )
        client_type = AsyncHTTPTransport if boundary == "transport" else AsyncLyraClient
        async with client_type("example.test") as client:
            operation = (
                client.download(
                    "report/download", path, operation="download", atomic=True
                )
                if isinstance(client, AsyncHTTPTransport)
                else client.results.download_table("a" * 32, "rows", path)
                if boundary == "tables"
                else client.results.download_location_input("a" * 32, "rows", path)
                if boundary == "inputs"
                else client.reports.download("a" * 32, path)
            )
            task = asyncio.create_task(operation)
            await harness.entered.wait()
            candidates = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
            temporary = next(candidate for candidate in candidates if candidate != path)
            task.cancel()
            await harness.closing.wait()
            assert not task.done()
            # File closure flushes partial bytes before awaited response cleanup.
            assert temporary.read_bytes() == b"partial"
            assert path.read_bytes() == b"original" if existing else not path.exists()
            harness.release.set()
            with pytest.raises(
                ValueError if cleanup_fails else asyncio.CancelledError
            ) as caught:
                await task
            if cleanup_fails:
                assert isinstance(caught.value.__context__, asyncio.CancelledError)
            assert harness.cleaned
            assert harness.tasks == [task, task]
            route = (
                "/report/download"
                if boundary == "transport"
                else f"/results/{'a' * 32}/tables/download"
                if boundary == "tables"
                else f"/results/{'a' * 32}/location-inputs/download"
                if boundary == "inputs"
                else f"/executions/{'a' * 32}/report/download"
            )
            assert harness.calls == [("GET", route)]

    asyncio.run(exercise())
    assert path.read_bytes() == b"original" if existing else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if existing else [])


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize(
    ("method", "interface"),
    [("table", "tables"), ("location_input", "location-inputs")],
)
def test_table_read_cancellation_settles_in_caller(
    monkeypatch: pytest.MonkeyPatch, method: str, interface: str, *, cleanup_fails: bool
) -> None:
    async def exercise() -> None:
        harness = CancellationHarness("download", cleanup_fails=cleanup_fails)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(harness.respond)),
        )
        async with AsyncLyraClient("example.test") as client:
            task = asyncio.create_task(
                getattr(client.results, method)("a" * 32, "rows")
            )
            await harness.entered.wait()
            task.cancel()
            await harness.closing.wait()
            assert not task.done()
            harness.release.set()
            with pytest.raises(ValueError if cleanup_fails else asyncio.CancelledError):
                await task
            assert harness.cleaned
            assert harness.tasks == [task, task]
            assert harness.calls == [
                ("GET", f"/results/{'a' * 32}/{interface}/download")
            ]

    asyncio.run(exercise())
