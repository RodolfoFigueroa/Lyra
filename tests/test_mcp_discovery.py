"""MCP projects the captured family catalog without acquiring resources."""

import asyncio
from unittest.mock import Mock

import pytest

from lyra.runtime.planning.discovery import Discovery
from lyra.server.mcp.tools import InProcessLyraBackend
from test_support.edition_catalog import edition_manifests
from test_support.manifest_catalog import compile_catalog
from test_support.mcp import call_tool


@pytest.mark.parametrize("count", [1, 2])
def test_direct_metric_dispatch(count: int) -> None:
    manifests = edition_manifests(count)
    view = Discovery(compile_catalog(manifests))
    backend = InProcessLyraBackend(lambda: Mock(catalog=Mock(discovery=view)))

    def call(name: str, **arguments: str) -> dict:
        result = asyncio.run(call_tool(backend, name, arguments))
        assert not result.get("isError", False)
        return result["structuredContent"]

    listing = call("lyra_list_metrics")
    assert listing["metrics"] == [
        item.model_dump(mode="json") for item in view.search()
    ]
    assert (
        call("lyra_search_metrics", query="measure")["candidates"] == listing["metrics"]
    )
    for index in range(count):
        name = f"measure_{index}"
        assert call("lyra_get_metric", metric=name) == view.inspect(name).model_dump(
            mode="json"
        )
    assert manifests["benchmark"].nodes[0].kind == "metric"
    manifests["benchmark"].nodes[0].private = True
    view = Discovery(compile_catalog(manifests))
    assert call("lyra_list_metrics")["total_count"] == count - 1
    for arguments in (
        {"metric": "measure_0"},
        {"metric": "unknown"},
        {"metric": "measure_1", "edition": "1"},
    ):
        assert asyncio.run(call_tool(backend, "lyra_get_metric", arguments))["isError"]
