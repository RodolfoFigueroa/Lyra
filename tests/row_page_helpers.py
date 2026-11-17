"""Shared exact HTTP and MCP row response budgets."""

import pytest
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from pydantic import BaseModel

from lyra.contracts.results import ResultRowPage
from lyra.server.mcp.serialization import mcp_response_budget
from lyra.server.response_budget import ResponseBudget, http_response_budget


@pytest.fixture(params=["http", "mcp"])
def budget(request: pytest.FixtureRequest) -> ResponseBudget[BaseModel]:
    if request.param == "http":
        return http_response_budget()

    def rows() -> ResultRowPage:
        raise NotImplementedError

    return mcp_response_budget(func_metadata(rows), 'request-雪"\\')
