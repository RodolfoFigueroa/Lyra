"""Durable execution reports across authenticated HTTP, Python, and MCP."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import httpx
import pytest
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from lyra.api import AsyncLyraClient, LyraClient
from lyra.contracts.errors import (
    ReportIntegrityError,
    ReportNotFoundError,
)
from lyra.contracts.reports import CompleteExecutionReport, ExecutionReport
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.server.mcp.serialization import mcp_response_size
from lyra.server.mcp.server import LyraFastMCP
from lyra.server.response_budget import (
    MAX_RETRIEVAL_BYTES,
    ResponseBudget,
    ResponseTooLargeError,
    http_response_budget,
    http_response_size,
)
from lyra.server.results import inspect_report
from lyra.server.routes import reports as report_routes
from tests.mcp_helpers import mcp_headers, tool_call_payload
from tests.report_access_helpers import BASE as _BASE
from tests.report_access_helpers import TEXT as _TEXT
from tests.report_access_helpers import Retained, retained
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["database", "retained"]


@pytest.mark.parametrize(
    "retained",
    [
        "small",
        "parameters",
        "graph",
        "hybrid",
        "zero",
        "location",
        "empty",
        "unpartitioned",
    ],
    indirect=True,
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_complete_report_http_and_clients(
    retained: Retained, database: Database, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    expected = retained.report
    before = deepcopy(database.rows)
    path = f"/executions/{expected.job_id}/report"
    with asyncio.Runner() as runner:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public), base_url=_BASE + "/"
        )
        inspection = runner.run(
            http.get(_BASE + path + "/inspection", headers=mcp_headers())
        )
        assert inspection.status_code == 200
        assert len(inspection.content) <= MAX_RETRIEVAL_BYTES
        complete_size = http_response_size(CompleteExecutionReport(report=expected))
        if complete_size <= MAX_RETRIEVAL_BYTES:
            assert inspection.json() == {
                "kind": "complete",
                "report": expected.model_dump(mode="json"),
            }
        else:
            assert inspection.json() == {
                "kind": "preview",
                "job_id": expected.job_id,
                "metric": expected.request.metric,
                "status": "succeeded",
                "omitted": ["report"],
                "download_url": _BASE + path + "/download",
            }
        download = runner.run(
            http.get(_BASE + path + "/download", headers=mcp_headers())
        )
        assert download.headers["content-type"] == "application/json"
        assert download.json() == expected.model_dump(mode="json")
        for suffix in ("/inspection", "/download"):
            assert runner.run(http.get(_BASE + path + suffix)).status_code == 401
            assert (
                runner.run(
                    http.get(_BASE + path + suffix, headers=mcp_headers("wrong"))
                ).status_code
                == 403
            )

        requests: list[httpx.Request] = []

        def send(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            response = runner.run(http.send(request))
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        async def async_send(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return await http.send(request)

        monkeypatch.setattr(
            httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(send))
        )
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(async_send)),
        )

        def fetch() -> ExecutionReport:
            if mode == "sync":
                with LyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    inspected = client.reports.inspect(expected.job_id)
                    assert inspected.kind == inspection.json()["kind"]
                    if inspected.kind == "complete":
                        assert inspected.report == expected
                    return client.reports.get(expected.job_id)

            async def run() -> ExecutionReport:
                async with AsyncLyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    inspected = await client.reports.inspect(expected.job_id)
                    assert inspected.kind == inspection.json()["kind"]
                    if inspected.kind == "complete":
                        assert inspected.report == expected
                    return await client.reports.get(expected.job_id)

            return runner.run(run())

        first = fetch()
        assert first == expected
        assert [str(r.url) for r in requests] == [
            _BASE + path + "/inspection",
            _BASE + path + "/download",
        ]
        assert all(
            r.headers["authorization"] == "Bearer agent-secret" for r in requests
        )
        first.request.parameters.clear()
        first.nodes[0].parameters.clear()
        first.nodes.clear()
        assert fetch() == expected
        runner.run(http.aclose())
    assert database.rows == before


def test_http_boundary_uses_encoded_response(
    retained: Retained, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = retained.report
    expected.request.parameters["escaped"] = _TEXT * 200
    expected.nodes[0].parameters["escaped"] = _TEXT * 200
    monkeypatch.setattr(
        retained.runtime.results,
        "read_report",
        lambda _: expected.model_copy(deep=True),
    )
    size = http_response_size(CompleteExecutionReport(report=expected))
    original_budget = report_routes.http_response_budget

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public)
        ) as client:
            for limit, kind in ((size, "complete"), (size - 1, "preview")):
                monkeypatch.setattr(
                    report_routes,
                    "http_response_budget",
                    partial(original_budget, limit=limit),
                )
                response = await client.get(
                    f"{_BASE}/executions/{expected.job_id}/report/inspection",
                    headers=mcp_headers(),
                )
                assert response.json()["kind"] == kind
                assert len(response.content) <= limit
                if kind == "complete":
                    assert len(response.content) == size

    asyncio.run(run())


@pytest.mark.parametrize(
    "damage", ["invalid", "absent", "store", "description", "report"]
)
@pytest.mark.parametrize("suffix", ["inspection", "download"])
def test_report_domain_errors(
    retained: Retained, database: Database, damage: str, suffix: str
) -> None:
    job_id = retained.report.job_id
    if damage == "invalid":
        job_id = "invalid"
        expected = None
    elif damage == "absent":
        job_id = "0" * 32
        expected = ReportNotFoundError(job_id)
    elif damage == "store":
        retained.runtime.config.result_store_path.unlink()
        expected = ReportNotFoundError(job_id)
    else:
        expected = ReportIntegrityError(job_id)
        database.rows["final_results"][0][damage] = None

    async def run() -> None:
        mcp_app = retained.app.state.mcp_app
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public)
            ) as client,
        ):
            response = await client.get(
                f"{_BASE}/executions/{job_id}/report/{suffix}",
                headers=mcp_headers(),
            )
            if expected is None:
                assert response.status_code == 422
                assert response.json()["detail"][0]["loc"] == ["path", "job_id"]
                return
            payload = error_payload(expected)
            assert response.status_code == error_status(payload)
            assert response.json() == {"detail": payload.model_dump()}
            mcp = await client.post(
                _BASE + "/mcp/",
                headers=mcp_headers(),
                json=tool_call_payload("lyra_inspect_report", {"job_id": job_id}),
            )
            assert mcp.json()["result"]["isError"]
            assert mcp.json()["result"]["structuredContent"] == {
                "error": payload.model_dump()
            }

    asyncio.run(run())


@pytest.mark.parametrize(
    "retained",
    [
        "small",
        "parameters",
        "graph",
        "hybrid",
        "zero",
        "location",
        "empty",
        "unpartitioned",
    ],
    indirect=True,
)
def test_mcp_report_sdk_and_boundaries(
    retained: Retained, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = retained.report
    limit = 2**63 - 1  # Measure the full response using the actual SDK transport.
    original = LyraFastMCP.retrieval_budget

    def budget(server: LyraFastMCP, name: str) -> ResponseBudget[BaseModel]:
        return replace(original(server, name), limit=limit)

    monkeypatch.setattr(LyraFastMCP, "retrieval_budget", budget)

    async def run() -> None:
        nonlocal limit
        mcp_app = retained.app.state.mcp_app
        async with (
            mcp_app.router.lifespan_context(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
        ):
            url = _BASE + "/mcp/"
            response = await http.post(
                url,
                json=tool_call_payload(
                    "lyra_inspect_report", {"job_id": expected.job_id}
                ),
            )
            body = response.json()["result"]["structuredContent"]["result"]
            assert json.loads(response.json()["result"]["content"][0]["text"]) == body
            assert len(response.content) <= limit
            actual = CallToolResult.model_validate(response.json()["result"])
            assert mcp_response_size(actual, 10) == len(response.content)
            assert body["kind"] == "complete"
            default_kind = (
                "complete"
                if len(response.content) <= MAX_RETRIEVAL_BYTES
                else "preview"
            )
            assert body["report"] == expected.model_dump(mode="json")
            limit = len(response.content)
            exact = await http.post(
                url,
                json=tool_call_payload(
                    "lyra_inspect_report", {"job_id": expected.job_id}
                ),
            )
            assert exact.content == response.content
            limit -= 1
            response = await http.post(
                url,
                json=tool_call_payload(
                    "lyra_inspect_report", {"job_id": expected.job_id}
                ),
            )
            body = response.json()["result"]["structuredContent"]["result"]
            assert body == {
                "kind": "preview",
                "job_id": expected.job_id,
                "metric": expected.request.metric,
                "status": "succeeded",
                "omitted": ["report"],
                "download_url": (
                    f"{_BASE}/executions/{expected.job_id}/report/download"
                ),
            }
            assert len(response.content) <= limit
            assert (await http.get(body["download_url"])).json() == expected.model_dump(
                mode="json"
            )
            assert (
                await http.get(body["download_url"], headers={"Authorization": ""})
            ).status_code == 401
            limit = MAX_RETRIEVAL_BYTES
            await _check_sdk(http, url, expected, default_kind)

    asyncio.run(run())


async def _check_sdk(
    http: httpx.AsyncClient, url: str, expected: ExecutionReport, kind: str
) -> None:
    async with (
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tool = next(
            t
            for t in (await session.list_tools()).tools
            if t.name == "lyra_inspect_report"
        )
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint
        assert tool.inputSchema["required"] == ["job_id"]
        assert tool.inputSchema["additionalProperties"] is False
        result = await session.call_tool(tool.name, {"job_id": expected.job_id})
        assert not result.isError
        validate(result.structuredContent, tool.outputSchema)
        assert result.structuredContent is not None
        assert result.structuredContent["result"]["kind"] == kind
        for arguments in (
            {},
            {"job_id": 5},
            {"job_id": "bad"},
            {"job_id": expected.job_id, "extra": 1},
        ):
            invalid = await session.call_tool(tool.name, arguments)
            assert invalid.isError
            assert invalid.structuredContent is None
        for reference, error in (("0" * 32, ReportNotFoundError("0" * 32)),):
            failed = await session.call_tool(tool.name, {"job_id": reference})
            assert failed.isError
            assert failed.structuredContent == {
                "error": error_payload(error).model_dump()
            }


def test_fixed_preview_must_fit(retained: Retained) -> None:
    with pytest.raises(ResponseTooLargeError):
        inspect_report(
            retained.report,
            public_api_base_url=_BASE,
            budget=http_response_budget(limit=1),
        )
