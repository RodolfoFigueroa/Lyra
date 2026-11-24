"""Authenticated retained input access through real ASGI and MCP envelopes."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pyarrow as pa
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette

from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.main import create_app
from lyra.server.mcp.server import create_mcp_app
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.response_budget import MAX_RETRIEVAL_BYTES
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from tests.arrow_download_helpers import BASE
from tests.config_helpers import api_credentials, load_test_config
from tests.location_input_helpers import publish_inputs, retained
from tests.mcp_helpers import mcp_headers, tool_call_payload, tool_payload
from tests.sqlite_helpers import Database, database

Transport = tuple[FastAPI, Starlette, str, ResultService]

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.fixture
def transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Transport:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    writer = SQLiteStore(config.result_store_path)
    direct, pairs = retained(writer)
    empty = direct.model_copy(update={"records": ()})
    skipped = empty.model_copy(
        update={
            "description": empty.description.model_copy(
                update={"availability": "not_materialized"}
            )
        }
    )
    reference = publish_inputs(
        writer,
        {
            "a": direct,
            "pairs": pairs,
            "empty": empty,
            "skipped": skipped,
        },
        output_keys=("one", "two"),
    )
    config.result_store_path.touch()
    service = ResultService(Mock(spec=[]), config.result_store_path)
    app = create_app(config, credentials=api_credentials())
    runtime = ApplicationRuntime(
        config, Mock(spec=[]), Mock(), Mock(), service.jobs, api_credentials(), service
    )
    app.state.runtime = runtime
    mcp = create_mcp_app(
        agent_api_key="agent-secret",
        public_api_base_url=BASE,
        backend=InProcessLyraBackend(lambda: runtime),
    )
    for method in ("read_result", "read_report", "read_table", "read_location_input"):
        monkeypatch.setattr(
            ResultStore, method, Mock(side_effect=AssertionError(method))
        )
    return app, mcp, reference, service


def test_authenticated_links_and_streams(
    transport: Transport, database: Database
) -> None:
    app, mcp, ref, _ = transport

    async def run() -> None:
        async with (
            mcp.router.lifespan_context(mcp),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp), base_url="http://testserver"
            ) as tools,
        ):
            for name, count in (("a", 2), ("pairs", 2), ("empty", 0)):
                path = f"/results/{ref}/location-inputs/download?input_name={name}"
                assert (await http.get(path)).status_code == 401
                assert (
                    await http.get(path, headers=mcp_headers("wrong"))
                ).status_code == 403
                payload = tool_call_payload(
                    "lyra_download_location_input", {"job_id": ref, "input_name": name}
                )
                assert (await tools.post("/", json=payload)).status_code == 401
                assert (
                    await tools.post("/", headers=mcp_headers("wrong"), json=payload)
                ).status_code == 403
                database.connection.execute.reset_mock()
                response = await tools.post(
                    "/",
                    headers=mcp_headers(),
                    json=tool_call_payload(
                        "lyra_download_location_input",
                        {"job_id": ref, "input_name": name},
                    ),
                )
                assert all(
                    not source.name.startswith("data_")
                    for call in database.connection.execute.call_args_list
                    for source in call.args[0].get_final_froms()
                )
                link = tool_payload(response)
                assert link == {
                    "job_id": ref,
                    "input_name": name,
                    "download_url": f"{BASE}{path}",
                }
                download = await http.get(path, headers=mcp_headers())
                assert download.status_code == 200
                assert (
                    download.headers["content-type"]
                    == "application/vnd.apache.arrow.stream"
                )
                frame = gpd.GeoDataFrame.from_arrow(
                    pa.ipc.open_stream(download.content).read_all()
                )
                assert len(frame) == count
                assert frame.crs is not None
                assert frame.crs.to_epsg() == 4326
                assert "row_key" not in frame
                if name == "pairs":
                    assert set(
                        zip(frame.source_location_id, frame.location_id, strict=True)
                    ) == {("A", "a"), ("B", "a")}

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        ("a", "bad", {}, 422, "validation"),
        ("a", "f" * 32, {}, 404, "result_not_found"),
        ("metric_dependency", None, {}, 404, "result_not_found"),
        ("skipped", None, {}, 409, "location_input_unavailable"),
    ],
)
def test_domain_errors(
    transport: Transport, case: tuple[str, str | None, dict[str, str | int], int, str]
) -> None:
    name, reference, query, status, code = case
    app, mcp, ref, _ = transport
    reference = reference or ref

    async def run() -> None:
        async with (
            mcp.router.lifespan_context(mcp),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp), base_url="http://testserver"
            ) as tools,
        ):
            tool = "lyra_download_location_input"
            rest = await http.get(
                f"/results/{reference}/location-inputs/download",
                params={"input_name": name},
                headers=mcp_headers(),
            )
            assert rest.status_code == status
            error = rest.json()["detail"]
            if code == "validation":
                assert error[0]["loc"] == ["path", "job_id"]
            else:
                assert error["code"] == code
                assert error["input_name"] == name
                assert error["job_id"] == reference
                if code != "location_input_unavailable":
                    assert error["output_name"] is None
            response = await tools.post(
                "/",
                headers=mcp_headers(),
                json=tool_call_payload(
                    tool, {"job_id": reference, "input_name": name, **query}
                ),
            )
            assert response.json()["result"]["isError"]
            if code == "validation":
                assert not response.json()["result"].get("structuredContent")
            else:
                assert tool_payload(response) == {"error": error}

    asyncio.run(run())


def test_damaged_input_preflight(transport: Transport, database: Database) -> None:
    database.rows["input_selections"].clear()
    test_domain_errors(transport, ("a", None, {}, 500, "result_integrity_error"))


def test_tool_schemas_and_argument_validation(transport: Transport) -> None:
    _, mcp, ref, _ = transport

    async def run() -> None:
        async with (
            mcp.router.lifespan_context(mcp),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp), base_url="http://testserver"
            ) as tools,
        ):
            listed = await tools.post(
                "/",
                headers=mcp_headers(),
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            definitions = {t["name"]: t for t in listed.json()["result"]["tools"]}

            for name in ("lyra_download_location_input",):
                definition = definitions[name]
                assert definition["annotations"]["readOnlyHint"]
                assert definition["inputSchema"]["required"] == [
                    "job_id",
                    "input_name",
                ]
                invalid = await tools.post(
                    "/",
                    headers=mcp_headers(),
                    json=tool_call_payload(name, {"job_id": ref, "input_name": 1}),
                )
                result = invalid.json()["result"]
                assert result["isError"]
                assert "structuredContent" not in result

    asyncio.run(run())


@pytest.mark.parametrize("large", [None, "schema", "provider"])
def test_complete_or_preview_input_descriptions(
    transport: Transport, monkeypatch: pytest.MonkeyPatch, large: str | None
) -> None:
    app, mcp, ref, service = transport
    description = service.read_description(ref)
    assert (
        description.location_inputs["a"].provider_scope.description == "Target records"
    )
    source = description.location_inputs["pairs"].source_scope
    assert source is not None
    assert source.description == "Source records"
    if large:
        entry = description.location_inputs["a"].model_copy(deep=True)
        if large == "schema":
            entry.table_schema.columns[0].description = "雪" * 10000
        else:
            entry.provider_scope = entry.provider_scope.model_copy(
                update={"description": "雪" * 10000}
            )
        description.location_inputs.update({f"input-{i}": entry for i in range(30)})
    monkeypatch.setattr(
        service, "read_description", lambda _: description.model_copy(deep=True)
    )

    async def run() -> None:
        async with (
            mcp.router.lifespan_context(mcp),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as http,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp), base_url="http://testserver"
            ) as tools,
        ):
            rest = await http.get(f"/results/{ref}", headers=mcp_headers())
            wire = await tools.post(
                "/",
                headers=mcp_headers(),
                json=tool_call_payload("lyra_describe_result", {"job_id": ref}),
            )
            assert len(wire.content) <= MAX_RETRIEVAL_BYTES
            inspection = wire.json()["result"]["structuredContent"]["result"]
            assert rest.json() == description.model_dump(mode="json")
            assert json.loads(wire.json()["result"]["content"][0]["text"]) == inspection
            if large:
                assert inspection["kind"] == "preview"
                assert inspection["omitted"] == ["description"]
                assert "description" not in inspection
                assert inspection["download_url"] == f"{BASE}/results/{ref}"
            else:
                assert inspection["description"] == description.model_dump(mode="json")
            download = await http.get(f"/results/{ref}", headers=mcp_headers())
            assert download.json() == description.model_dump(mode="json")
            assert (
                download.json()["location_inputs"]["skipped"]["availability"]
                == "not_materialized"
            )

    asyncio.run(run())
