"""MCP calls validated against unmodified advertised wire schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jsonschema import validate

from docs.scripts.mcp_tools import list_tools, protocol_client

if TYPE_CHECKING:
    from lyra.server.mcp.tools import LyraMCPBackend


async def call_tool(
    backend: LyraMCPBackend, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call through MCP and validate the unmodified successful wire payload."""
    async with protocol_client(backend) as client:
        definitions = await list_tools(client)
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        response.raise_for_status()
        result = response.json()["result"]
        if not result.get("isError", False):
            schema = definitions[name].outputSchema
            assert schema is not None
            validate(result["structuredContent"], schema)
        return result
