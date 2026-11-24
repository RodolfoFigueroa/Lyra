"""Mocked native clients shared by retained table acceptance tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from functools import partial
from typing import TypeVar

import httpx
import pytest

from lyra.api import AsyncLyraClient, LyraClient

_T = TypeVar("_T")
_MEDIA = "application/vnd.apache.arrow.stream"


class ClientHarness:
    def __init__(self, mode: str, runner: asyncio.Runner) -> None:
        self.runner = runner
        self.responses: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []
        cls = LyraClient if mode == "sync" else AsyncLyraClient
        self.client = cls("example.test/api", agent_api_key="secret")

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.url.path.startswith(("/api/results/", "/api/jobs/"))
        return self.responses.pop(0)

    def resolve(self, value: _T | Awaitable[_T]) -> _T:
        if isinstance(value, Awaitable):

            async def wait() -> _T:
                return await value

            return self.runner.run(wait())
        return value

    def close(self) -> None:
        if isinstance(self.client, LyraClient):
            self.client.close()
        else:
            self.runner.run(self.client.aclose())


@pytest.fixture(params=["sync", "async"])
def client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ClientHarness]:
    with asyncio.Runner() as runner:

        def respond(request: httpx.Request) -> httpx.Response:
            return harness.respond(request)

        mock = httpx.MockTransport(respond)
        monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=mock))
        monkeypatch.setattr(
            httpx, "AsyncClient", partial(httpx.AsyncClient, transport=mock)
        )
        harness = ClientHarness(request.param, runner)
        try:
            yield harness
        finally:
            harness.close()


def arrow_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"Content-Type": _MEDIA})
