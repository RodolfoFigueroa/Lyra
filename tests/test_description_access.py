"""Durable metadata across authenticated HTTP, Python, and MCP interfaces."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING
from unittest.mock import Mock

import httpx
import pytest
import sqlalchemy as sa
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.api import AsyncLyraClient, LyraClient
from lyra.contracts.errors import (
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import (
    ResultDescription as StoredDescription,
)
from lyra.runtime.result_descriptions import (
    ResultDescriptions,
    ResultOutput,
)
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import loading
from lyra.sdk.output_schema import compile_output
from lyra.sdk.storage_schema import describe
from lyra.server.main import create_app
from lyra.server.mcp.serialization import mcp_response_size
from lyra.server.mcp.server import LyraFastMCP, create_mcp_app
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.response_budget import (
    MAX_RETRIEVAL_BYTES,
    ResponseBudget,
)
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from test_support.publication import publish_result
from tests.config_helpers import api_credentials, load_test_config
from tests.mcp_helpers import mcp_headers, tool_call_payload
from tests.metric_execution_helpers import schema
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from pydantic import BaseModel

    from lyra.contracts.results import ResultDescription

__all__ = ["database"]
_BASE = "https://lyra.example.test/public/api"
_TEXT = '雪 "\\\n'


@dataclass
class Retained:
    app: FastAPI
    public: Starlette
    runtime: ApplicationRuntime
    description: ResultDescription


def _publish(path: Path, shape: str) -> ResultDescription:
    declaration = schema(spatial=True)
    if shape == "schema":
        declaration = declaration.add_columns(
            {f"value_{i}": deepcopy(declaration.columns["value"]) for i in range(200)}
        )
    metadata = compile_output(declaration)[1]
    if shape == "empty":
        metadata.columns[0].description = _TEXT * 16000
    layout = describe(declaration, metadata=metadata)
    names = tuple(f"output_{i}" for i in range(120 if shape == "outputs" else 2))
    owner = ProviderScope(provider_id="places", code_revision="retained-revision")
    registrations = tuple(
        Registration.from_description("metric", name, layout) for name in names
    )
    with SQLiteStore(path) as store:
        store.initialize(registrations)
        stored = StoredDescription(
            metric_name="metric",
            output_names=names,
            code_revision="v1",
            parameters={"default": None, "nested": {"value": "original"}},
            outputs={
                registration.output_name: ResultOutput(
                    registration_key=registration.key,
                    owner_scope=owner,
                    computations=(),
                )
                for registration in registrations
            },
        )
        if shape == "parameters":
            stored.parameters["large"] = _TEXT * 16000
        reference = publish_result(ResultDescriptions(store), stored)
        # Even an oversized empty result publishes and reopens completely locally.
        result = ResultStore(store).read_result(reference)
        assert all(frame.empty for frame in result.tables.values())
        description = ResultStore(store).describe(reference)
        assert description.outputs == result.metadata.outputs
    path.touch()
    return description


@pytest.fixture
def retained(
    request: pytest.FixtureRequest,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Retained:
    config = load_test_config(tmp_path)
    config.api.public_base_url = _BASE
    config.mcp.enabled = True
    expected = _publish(config.result_store_path, getattr(request, "param", "small"))
    app = create_app(config, credentials=api_credentials())
    jobs = Mock()
    jobs.read_job.side_effect = AssertionError("Job expired: no job lookups")
    runtime = ApplicationRuntime(
        config,
        Mock(),
        Mock(),
        Mock(),
        jobs,
        api_credentials(),
        ResultService(jobs, config.result_store_path),
    )
    app.state.runtime = runtime
    # The active catalog has changed; retained declarations remain authoritative.
    runtime.catalog = Mock(spec=[], name="replacement plugin catalog")
    forbidden = Mock(side_effect=AssertionError("Description read isolation"))
    for target, names in (
        (MetricTables, ("read_connection",)),
        (ResultStore, ("read_result", "read_report", "read_table")),
        (ResultDescriptions, ("read_report",)),
        (loading, ("load_plugin_definition", "load_installed_plugin_definition")),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    database.rows["final_results"][0]["report"] = "{"
    execute = database.execute

    def metadata_only(statement: object, parameters: object = None) -> object:
        assert isinstance(statement, sa.sql.Select)
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        assert source.name in {
            "final_results",
            "storage_registrations",
            "stored_results",
        }
        if source.name == "final_results":
            assert "report" not in statement.selected_columns
        return execute(statement, parameters)

    database.connection.execute.side_effect = metadata_only
    return Retained(
        app, Starlette(routes=[Mount("/public/api", app)]), runtime, expected
    )


@pytest.mark.parametrize(
    "retained", ["small", "schema", "outputs", "parameters", "empty"], indirect=True
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_complete_description_http_and_clients(
    retained: Retained, database: Database, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    expected = retained.description
    before = deepcopy(database.rows)
    path = f"/results/{expected.job_id}"
    with asyncio.Runner() as runner:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public), base_url=_BASE + "/"
        )
        inspection = runner.run(http.get(_BASE + path, headers=mcp_headers()))
        assert inspection.status_code == 200
        assert inspection.json() == expected.model_dump(mode="json")
        assert runner.run(http.get(_BASE + path)).status_code == 401
        assert (
            runner.run(http.get(_BASE + path, headers=mcp_headers("wrong"))).status_code
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

        def fetch() -> ResultDescription:
            if mode == "sync":
                with LyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    return client.results.describe(expected.job_id)

            async def run() -> ResultDescription:
                async with AsyncLyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    return await client.results.describe(expected.job_id)

            return runner.run(run())

        first = fetch()
        assert first == expected
        assert [str(r.url) for r in requests] == [_BASE + path]
        assert all(
            r.headers["authorization"] == "Bearer agent-secret" for r in requests
        )
        first.parameters.clear()
        first.outputs["output_0"].table_schema.columns.clear()
        first.outputs["output_1"].owner_scope = None
        assert fetch() == expected
        runner.run(http.aclose())
    assert database.rows == before


@pytest.mark.parametrize(
    "damage", ["invalid", "absent", "store", "registration", "report"]
)
def test_description_domain_errors(
    retained: Retained, database: Database, damage: str
) -> None:
    reference = retained.description.job_id
    if damage == "invalid":
        reference = "invalid"
        expected = None
    elif damage == "absent":
        reference = "0" * 32
        expected = ResultNotFoundError(reference)
    elif damage == "store":
        retained.runtime.config.result_store_path.unlink()
        expected = ResultNotFoundError(reference)
    else:
        expected = ResultIntegrityError(reference)
        if damage == "registration":
            database.rows["storage_registrations"].clear()
        else:
            database.rows["final_results"][0]["report"] = None

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public)
        ) as client:
            response = await client.get(
                f"{_BASE}/results/{reference}", headers=mcp_headers()
            )
        if expected is None:
            assert response.status_code == 422
            assert response.json()["detail"][0]["loc"] == ["path", "job_id"]
            return
        payload = error_payload(expected)
        assert response.status_code == error_status(payload)
        assert response.json() == {"detail": payload.model_dump()}
        assert response.json()["detail"]["output_name"] is None

    asyncio.run(run())


@pytest.mark.parametrize("retained", ["small", "schema"], indirect=True)
def test_mcp_description_sdk_and_boundaries(
    retained: Retained, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = retained.description
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
                    "lyra_describe_result", {"job_id": expected.job_id}
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
            assert body["description"] == expected.model_dump(mode="json")
            limit = len(response.content)
            exact = await http.post(
                url,
                json=tool_call_payload(
                    "lyra_describe_result", {"job_id": expected.job_id}
                ),
            )
            assert exact.content == response.content
            limit -= 1
            response = await http.post(
                url,
                json=tool_call_payload(
                    "lyra_describe_result", {"job_id": expected.job_id}
                ),
            )
            body = response.json()["result"]["structuredContent"]["result"]
            assert body == {
                "kind": "preview",
                "job_id": expected.job_id,
                "omitted": ["description"],
                "download_url": (f"{_BASE}/results/{expected.job_id}"),
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


@pytest.mark.anyio
async def test_inline_description_with_oversized_preview_url(
    retained: Retained,
) -> None:
    app = create_mcp_app(
        agent_api_key="agent-secret",
        public_api_base_url=_BASE + "/" + "p" * 33000,
        backend=InProcessLyraBackend(lambda: retained.runtime),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://lyra.example.test"
        ) as http,
    ):
        response = await http.post(
            "/",
            headers=mcp_headers(),
            json=tool_call_payload(
                "lyra_describe_result", {"job_id": retained.description.job_id}
            ),
        )
    assert len(response.content) <= MAX_RETRIEVAL_BYTES
    assert response.json()["result"]["structuredContent"]["result"] == {
        "kind": "complete",
        "description": retained.description.model_dump(mode="json"),
    }


async def _check_sdk(
    http: httpx.AsyncClient, url: str, expected: ResultDescription, kind: str
) -> None:
    async with (
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tool = next(
            t
            for t in (await session.list_tools()).tools
            if t.name == "lyra_describe_result"
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
        for reference, error in (("0" * 32, ResultNotFoundError("0" * 32)),):
            failed = await session.call_tool(tool.name, {"job_id": reference})
            assert failed.isError
            assert failed.structuredContent == {
                "error": error_payload(error).model_dump()
            }
