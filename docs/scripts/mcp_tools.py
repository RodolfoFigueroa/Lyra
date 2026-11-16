"""In-process MCP protocol access for tests and reference generation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import Mock

import httpx
from mcp.types import Tool

from lyra.server.mcp.server import create_mcp_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from lyra.server.mcp.tools import LyraMCPBackend


@asynccontextmanager
async def protocol_client(
    backend: LyraMCPBackend,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Open an authenticated ASGI transport with its application lifespan.

    Yields:
        An HTTP client bound to the in-process MCP application.
    """
    app = create_mcp_app(
        agent_api_key="reference-secret",
        public_api_base_url="https://example.test",
        backend=backend,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "Authorization": "Bearer reference-secret",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
        ) as client,
    ):
        yield client


async def list_tools(client: httpx.AsyncClient) -> dict[str, Tool]:
    """Read the server's raw advertised tool definitions.

    Returns:
        Advertised tools keyed by name, with their wire schemas intact.
    """
    response = await client.post(
        "/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    response.raise_for_status()
    return {
        item["name"]: Tool.model_validate(item)
        for item in response.json()["result"]["tools"]
    }


def advertised_tools() -> dict[str, Tool]:
    """Enumerate tools with backend operations that fail if invoked.

    Returns:
        Tool definitions obtained without accessing backend resources.
    """
    backend = Mock()
    for name in ("get_discovery", "create_job", "observe_job", "describe_result"):
        getattr(backend, name).side_effect = AssertionError(
            "Tool enumeration must not invoke backend operations"
        )

    async def enumerate_tools() -> dict[str, Tool]:
        async with protocol_client(backend) as client:
            return await list_tools(client)

    return asyncio.run(enumerate_tools())
