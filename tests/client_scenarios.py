from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar, cast

import aiohttp
import pytest

from lyra.api import AsyncLyraAdminClient, AsyncLyraClient, LyraAdminClient, LyraClient
from tests.client_helpers import (
    ClientRuntime,
    FakeAsyncResponse,
    FakeContent,
    FakeSession,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from lyra.contracts.types import JsonValue


_T = TypeVar("_T")


@dataclass
class Reply:
    status: int = 200
    payload: JsonValue = None
    text: str | None = None
    lines: list[str] = field(default_factory=list)
    chunks: list[bytes] = field(default_factory=list)
    headers: dict[str, str] = field(
        default_factory=lambda: {"content-type": "application/json"}
    )
    read_error: BaseException | None = None
    on_line: Callable[[], None] = lambda: None
    closed: bool = False


class AsyncContent(FakeContent):
    def __init__(self, reply: Reply) -> None:
        super().__init__(lines=reply.lines, chunks=reply.chunks)
        self.reply = reply

    async def _iter_lines(self) -> AsyncIterator[bytes]:
        async for line in super()._iter_lines():
            self.reply.on_line()
            yield line
        if self.reply.read_error is not None:
            raise self.reply.read_error


class AsyncReply(FakeAsyncResponse):
    def __init__(self, reply: Reply) -> None:
        super().__init__(
            status=reply.status, payload=reply.payload, headers=reply.headers
        )
        self.reply = reply
        self.content = AsyncContent(reply)

    async def text(self) -> str:
        return self.reply.text if self.reply.text is not None else await super().text()

    async def __aexit__(self, *args: object) -> None:
        self.reply.closed = True


@dataclass
class Scenario:
    runtime: ClientRuntime
    asynchronous: bool
    replies: list[Reply | BaseException] = field(default_factory=list)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    timeouts: list[aiohttp.ClientTimeout] = field(default_factory=list)
    now: float = 0.0

    def resolve(self, value: _T | Awaitable[_T]) -> _T:
        return (
            self.runtime.run(value) if inspect.isawaitable(value) else cast("_T", value)
        )

    @property
    def client(self) -> LyraClient | AsyncLyraClient:
        cls = AsyncLyraClient if self.asynchronous else LyraClient
        return cls("example.test", agent_api_key="consumer")

    @property
    def admin(self) -> LyraAdminClient | AsyncLyraAdminClient:
        cls = AsyncLyraAdminClient if self.asynchronous else LyraAdminClient
        return cls("example.test", admin_api_key="admin")

    def request(self, method: str, url: str, **kwargs: object) -> Reply:
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def scenario(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    client_runtime: ClientRuntime,
) -> Scenario:
    scenario = Scenario(client_runtime, asynchronous=request.param)

    class Session(FakeSession):
        def request(self, *args: object, **kwargs: object) -> AsyncReply:
            assert isinstance(self, Session)
            scenario.timeouts.append(cast("aiohttp.ClientTimeout", kwargs["timeout"]))
            return AsyncReply(scenario.request(str(args[0]), str(args[1]), **kwargs))

        def get(self, *args: object, **kwargs: object) -> AsyncReply:
            return self.request("GET", *args, **kwargs)

    async def sleep(seconds: float) -> None:
        scenario.sleep(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    monkeypatch.setattr(
        "lyra.api.client.polling.time", SimpleNamespace(monotonic=lambda: scenario.now)
    )
    monkeypatch.setattr(
        "lyra.api.client.polling.random.SystemRandom.uniform",
        lambda _self, _low, high: high,
    )
    monkeypatch.setattr(
        "lyra.api.client.sync.time", SimpleNamespace(sleep=scenario.sleep)
    )
    monkeypatch.setattr(
        "lyra.api.client.async_.asyncio",
        SimpleNamespace(
            sleep=sleep,
            get_running_loop=asyncio.get_running_loop,
            shield=asyncio.shield,
        ),
    )
    return scenario
