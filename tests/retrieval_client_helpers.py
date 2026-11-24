"""Run both public Python clients against the same in-process HTTP application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Generator, Iterator
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, TypeVar

import httpx
import pytest

from lyra.api import AsyncLyraClient, LyraClient
from tests.arrow_download_helpers import BASE

if TYPE_CHECKING:
    from starlette.applications import Starlette

_T = TypeVar("_T")


class NativeAPI:
    def __init__(self, mode: str, runner: asyncio.Runner) -> None:
        self.mode = mode
        self.runner = runner
        self.requests: list[httpx.Request] = []

    def resolve(self, value: _T | Awaitable[_T]) -> _T:
        if isinstance(value, Awaitable):

            async def wait() -> _T:
                return await value

            return self.runner.run(wait())
        return value

    @contextmanager
    def connect(self) -> Generator[LyraClient | AsyncLyraClient]:
        cls = LyraClient if self.mode == "sync" else AsyncLyraClient
        client = cls(BASE.removeprefix("https://"), agent_api_key="agent-secret")
        try:
            yield client
        finally:
            if isinstance(client, LyraClient):
                client.close()
            else:
                self.resolve(client.aclose())

    @contextmanager
    def serve(
        self, app: Starlette, monkeypatch: pytest.MonkeyPatch
    ) -> Generator[httpx.AsyncClient]:
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

        async def send(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return await http.send(request)

        def send_sync(request: httpx.Request) -> httpx.Response:
            response = self.runner.run(send(request))
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        with monkeypatch.context() as patch:
            patch.setattr(
                httpx,
                "Client",
                partial(httpx.Client, transport=httpx.MockTransport(send_sync)),
            )
            patch.setattr(
                httpx,
                "AsyncClient",
                partial(httpx.AsyncClient, transport=httpx.MockTransport(send)),
            )
            try:
                yield http
            finally:
                self.resolve(http.aclose())


@pytest.fixture(params=["sync", "async"])
def native_api(request: pytest.FixtureRequest) -> Iterator[NativeAPI]:
    with asyncio.Runner() as runner:
        yield NativeAPI(request.param, runner)
