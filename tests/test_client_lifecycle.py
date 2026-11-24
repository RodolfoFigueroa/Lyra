"""Session ownership, cancellation, and deadline behavior without network access."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import httpx
import pytest

from lyra.api import (
    AsyncLyraAdminClient,
    AsyncLyraClient,
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
from tests.job_id_helpers import CLIENT_JOB_ID

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


pytestmark = pytest.mark.usefixtures("sessions")


class Session(FakeSession):
    instances: ClassVar[list[Session]] = []

    def __init__(self, *, timeout: float | httpx.Timeout | None = 30.0) -> None:
        super().__init__(timeout=timeout)
        self.is_closed = False
        self.calls: list[str] = []
        self.instances.append(self)

    def dispatch(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        url = str(args[1])
        if url.endswith("/download"):
            return self.get(*args[1:], **kwargs)
        self.calls.append(url)
        if url.endswith("/live"):
            return FakeAsyncResponse(payload=_liveness_response())
        if args[0] == "POST":
            return FakeAsyncResponse(status=202, payload=_job_response())
        if url.endswith("/result"):
            return FakeAsyncResponse(payload=_result_response())
        return FakeAsyncResponse(payload=_status_response("succeeded"))

    def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        assert kwargs["headers"] == {"Authorization": "Bearer agent"}
        self.calls.append(str(args[0]))
        return FakeAsyncResponse(chunks=[b"result"])


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Session]]:
    Session.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", Session)
    yield Session.instances
    assert all(session.is_closed for session in Session.instances)


@pytest.mark.parametrize("cls", [AsyncLyraClient, AsyncLyraAdminClient])
def test_async_context_concurrent_use_and_close(
    cls: type[AsyncLyraClient | AsyncLyraAdminClient], sessions: list[Session]
) -> None:
    client = cls("example.test")
    assert all(not session.is_closed for session in sessions)

    async def run() -> None:
        async with client:
            assert len(sessions) == 1
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
    assert sessions[0].is_closed


def test_close_unused_clients_is_harmless(sessions: list[Session]) -> None:
    async def run() -> None:
        for cls in (AsyncLyraClient, AsyncLyraAdminClient):
            asynchronous = cls("example.test")
            await asynchronous.aclose()
            await asynchronous.aclose()
        assert all(session.is_closed for session in sessions)

    asyncio.run(run())


def test_async_session_spans_submission_wait_and_download(
    tmp_path: Path, sessions: list[Session]
) -> None:
    async def run() -> None:
        async with AsyncLyraClient("example.test", agent_api_key="agent") as client:
            handle = await client.jobs.submit("metric", inputs={}, parameters={})
            await handle.wait()
            await client.results.download(handle.job_id, tmp_path / "result")
            assert len(sessions) == 1
            assert len(sessions[0].calls) == 3
        for operation in (handle.status(), handle.result(), handle.wait(timeout=0)):
            with pytest.raises(RuntimeError, match="closed"):
                await operation

    asyncio.run(run())
    assert (tmp_path / "result").read_bytes() == b"result"


def test_async_run_to_file_downloads_once_after_completion(
    tmp_path: Path, sessions: list[Session]
) -> None:
    target = tmp_path / "result.json"

    async def run() -> None:
        async with AsyncLyraClient("example.test", agent_api_key="agent") as client:
            await client.jobs.run_to_file(
                "metric", path=target, options=RunOptions(timeout=1)
            )

    asyncio.run(run())
    assert target.read_bytes() == b"result"
    assert sessions[0].calls == [
        "https://example.test/jobs",
        f"https://example.test/jobs/{CLIENT_JOB_ID}",
        f"https://example.test/jobs/{CLIENT_JOB_ID}/result/download",
    ]
