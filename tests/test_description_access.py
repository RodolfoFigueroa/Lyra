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
    InvalidResultReferenceError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.results import CompleteResultDescription, ResultDescription
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
from lyra.server.mcp.server import LyraFastMCP
from lyra.server.response_budget import (
    MAX_RETRIEVAL_BYTES,
    ResponseBudget,
    http_response_size,
)
from lyra.server.results import ResultService
from lyra.server.routes import results as result_routes
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
    path = f"/results/{expected.result_ref}"
    with asyncio.Runner() as runner:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public), base_url=_BASE + "/"
        )
        inspection = runner.run(http.get(_BASE + path, headers=mcp_headers()))
        assert inspection.status_code == 200
        assert len(inspection.content) <= MAX_RETRIEVAL_BYTES
        complete_size = http_response_size(
            CompleteResultDescription(description=expected)
        )
        if complete_size <= MAX_RETRIEVAL_BYTES:
            assert inspection.json() == {
                "kind": "complete",
                "description": expected.model_dump(mode="json"),
            }
        else:
            assert inspection.json() == {
                "kind": "preview",
                "result_ref": expected.result_ref,
                "execution_id": expected.execution_id,
                "omitted": ["description"],
                "download_url": _BASE + path + "/description/download",
            }
        download = runner.run(
            http.get(_BASE + path + "/description/download", headers=mcp_headers())
        )
        assert download.headers["content-type"] == "application/json"
        assert download.json() == expected.model_dump(mode="json")
        for suffix in ("", "/description/download"):
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

        def fetch() -> ResultDescription:
            if mode == "sync":
                with LyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    return client.results.describe(expected.result_ref)

            async def run() -> ResultDescription:
                async with AsyncLyraClient(
                    _BASE.removeprefix("https://"), agent_api_key="agent-secret"
                ) as client:
                    return await client.results.describe(expected.result_ref)

            return runner.run(run())

        first = fetch()
        assert first == expected
        assert [str(r.url) for r in requests] == (
            [_BASE + path]
            if complete_size <= MAX_RETRIEVAL_BYTES
            else [_BASE + path, _BASE + path + "/description/download"]
        )
        assert all(
            r.headers["authorization"] == "Bearer agent-secret" for r in requests
        )
        first.parameters.clear()
        first.outputs["output_0"].table_schema.columns.clear()
        first.outputs["output_1"].owner_scope = None
        assert fetch() == expected
        runner.run(http.aclose())
    assert database.rows == before


def test_http_boundary_uses_encoded_response(
    retained: Retained, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = retained.description
    expected.parameters["escaped"] = _TEXT * 200
    monkeypatch.setattr(
        retained.runtime.results,
        "read_description",
        lambda _: expected.model_copy(deep=True),
    )
    size = http_response_size(CompleteResultDescription(description=expected))
    original_budget = result_routes.http_response_budget

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public)
        ) as client:
            for limit, kind in ((size, "complete"), (size - 1, "preview")):
                monkeypatch.setattr(
                    result_routes,
                    "http_response_budget",
                    partial(original_budget, limit=limit),
                )
                response = await client.get(
                    f"{_BASE}/results/{expected.result_ref}", headers=mcp_headers()
                )
                assert response.json()["kind"] == kind
                assert len(response.content) <= limit
                if kind == "complete":
                    assert len(response.content) == size

    asyncio.run(run())


@pytest.mark.parametrize(
    "damage", ["invalid", "absent", "store", "registration", "report"]
)
@pytest.mark.parametrize("suffix", ["", "/description/download"])
def test_description_domain_errors(
    retained: Retained, database: Database, damage: str, suffix: str
) -> None:
    reference = retained.description.result_ref
    if damage == "invalid":
        reference = "invalid"
        expected = InvalidResultReferenceError(reference)
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
                f"{_BASE}/results/{reference}{suffix}", headers=mcp_headers()
            )
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
    observed: list[int] = []
    limit = MAX_RETRIEVAL_BYTES
    original = LyraFastMCP.retrieval_budget

    def budget(server: LyraFastMCP, name: str) -> ResponseBudget[BaseModel]:
        value = replace(original(server, name), limit=limit)

        def measure(candidate: BaseModel) -> int:
            size = value.size(candidate)
            observed.append(size)
            return size

        return replace(value, size=measure)

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
                    "lyra_describe_result", {"result_ref": expected.result_ref}
                ),
            )
            body = response.json()["result"]["structuredContent"]["result"]
            assert json.loads(response.json()["result"]["content"][0]["text"]) == body
            assert len(response.content) <= limit
            actual = CallToolResult.model_validate(response.json()["result"])
            assert mcp_response_size(actual, 10) == len(response.content)
            if body["kind"] == "complete":
                assert body["description"] == expected.model_dump(mode="json")
                assert observed[-1] == len(response.content)
                limit = len(response.content)
                exact = await http.post(
                    url,
                    json=tool_call_payload(
                        "lyra_describe_result", {"result_ref": expected.result_ref}
                    ),
                )
                assert exact.content == response.content
                limit -= 1
                response = await http.post(
                    url,
                    json=tool_call_payload(
                        "lyra_describe_result", {"result_ref": expected.result_ref}
                    ),
                )
                body = response.json()["result"]["structuredContent"]["result"]
            assert body == {
                "kind": "preview",
                "result_ref": expected.result_ref,
                "execution_id": expected.execution_id,
                "omitted": ["description"],
                "download_url": (
                    f"{_BASE}/results/{expected.result_ref}/description/download"
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
            kind = "complete" if observed[-1] <= limit else "preview"
            await _check_sdk(http, url, expected, kind)

    asyncio.run(run())


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
        assert tool.inputSchema["required"] == ["result_ref"]
        assert tool.inputSchema["additionalProperties"] is False
        result = await session.call_tool(tool.name, {"result_ref": expected.result_ref})
        assert not result.isError
        validate(result.structuredContent, tool.outputSchema)
        assert result.structuredContent is not None
        assert result.structuredContent["result"]["kind"] == kind
        for arguments in (
            {},
            {"result_ref": 5},
            {"result_ref": expected.result_ref, "extra": 1},
        ):
            invalid = await session.call_tool(tool.name, arguments)
            assert invalid.isError
            assert invalid.structuredContent is None
        for reference, error in (
            ("bad", InvalidResultReferenceError("bad")),
            ("0" * 32, ResultNotFoundError("0" * 32)),
        ):
            failed = await session.call_tool(tool.name, {"result_ref": reference})
            assert failed.isError
            assert failed.structuredContent == {
                "error": error_payload(error).model_dump()
            }
