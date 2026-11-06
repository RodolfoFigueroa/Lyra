"""MCP projects the captured family catalog without acquiring resources."""

import asyncio
from unittest.mock import Mock

import pytest
from jsonschema import validate

from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.server.mcp.models import TOOL_CONTRACTS_BY_NAME
from lyra.server.mcp.tools import InProcessLyraBackend, ToolCallError, execute_tool
from test_support.edition_catalog import edition_manifests


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("implicit", [False, True])
def test_family_dispatch(count: int, *, implicit: bool) -> None:
    manifests = edition_manifests(count)
    nodes = manifests["benchmark"].nodes
    if implicit:
        nodes[:] = nodes[:1]
        assert nodes[0].kind == "metric"
        nodes[0].edition = None
    view = Discovery(compile_catalog(manifests))
    backend = InProcessLyraBackend(lambda: Mock(catalog=Mock(discovery=view)))

    def call(name: str, **arguments: str) -> dict:
        contract = TOOL_CONTRACTS_BY_NAME[name]
        output = asyncio.run(
            execute_tool(
                name,
                contract.input_model.model_validate(arguments),
                backend,
                public_api_base_url="https://example.test",
            )
        ).model_dump(mode="json")
        validate(output, contract.output_schema)
        return output

    listing = call("lyra_list_metrics")
    assert listing["metrics"] == [
        item.model_dump(mode="json") for item in view.search()
    ]
    assert (
        call("lyra_search_metrics", query="measure")["candidates"] == listing["metrics"]
    )
    assert call("lyra_get_metric", metric="measure") == view.inspect(
        "measure"
    ).model_dump(mode="json")
    if not implicit:
        for edition in range(count):
            assert call(
                "lyra_get_metric", metric="measure", edition=str(edition)
            ) == view.inspect("measure", edition=str(edition)).model_dump(mode="json")
    if not implicit and count == 2:
        choices = call("lyra_search_metrics", query="1")["candidates"][0]["editions"]
        assert [choice["edition"] for choice in choices] == ["1"]
        concrete = call("lyra_get_metric", metric="measure", edition="1")
        assert concrete["accepted_selections"]["places"] == [
            concrete["bindings"]["places"]
        ]
    assert nodes[0].kind == "metric"
    nodes[0].private = True
    view = Discovery(compile_catalog(manifests))
    assert call("lyra_list_metrics")["total_count"] == (
        0 if implicit or count == 1 else 1
    )
    with pytest.raises(ToolCallError, match="Unknown public"):
        call(
            "lyra_get_metric",
            metric="measure",
            **({} if implicit else {"edition": "0"}),
        )
