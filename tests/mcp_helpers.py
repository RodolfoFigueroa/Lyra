from __future__ import annotations

import asyncio
import json
import threading
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, Unpack

import httpx

if TYPE_CHECKING:
    from starlette.applications import Starlette

    from lyra.contracts.types import JsonObject


class _RequestOptions(TypedDict):
    headers: NotRequired[dict[str, str]]
    json: NotRequired[JsonObject]
    content: NotRequired[str]


def mcp_headers(bearer: str | None = None) -> dict[str, str]:
    token = "agent-secret" if bearer is None else bearer
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }


def tool_call_payload(
    name: str,
    arguments: dict[str, Any],
    *,
    request_id: int = 10,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def tool_payload(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200
    result = response.json()["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


class ManagedTestClient:
    def __init__(self, app: Starlette) -> None:
        self._app = app
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._client: httpx.AsyncClient | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5)

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with (
            self._app.router.lifespan_context(self._app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._app),
                base_url="http://testserver",
            ) as client,
        ):
            self._client = client
            self._ready.set()
            await self._stop.wait()

    def get(self, path: str, **kwargs: Unpack[_RequestOptions]) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Unpack[_RequestOptions]) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Unpack[_RequestOptions],
    ) -> httpx.Response:
        assert self._client is not None
        assert self._loop is not None
        request = self._client.request(method, path, **kwargs)
        return asyncio.run_coroutine_threadsafe(request, self._loop).result(timeout=10)

    def close(self) -> None:
        if self._client is None:
            return
        assert self._loop is not None
        assert self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()
        self._client = None
