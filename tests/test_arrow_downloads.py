"""Ordinary Arrow readers, durable HTTP/MCP access, and stream resource ownership."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager, nullcontext
from typing import TYPE_CHECKING
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pandas as pd
import pyarrow as pa
import pytest
import sqlalchemy as sa
from geopandas.testing import assert_geodataframe_equal
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from starlette.requests import ClientDisconnect

from lyra.contracts.errors import (
    InvalidResultReferenceError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.mcp.serialization import mcp_response_size
from lyra.server.response_budget import MAX_RETRIEVAL_BYTES
from lyra.server.results import ResultService
from lyra.server.table_response import TableResponse
from tests.arrow_download_helpers import BASE, Retained, retained
from tests.mcp_helpers import mcp_headers, tool_call_payload
from tests.retained_table_helpers import mixed_result
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from starlette.types import Message

__all__ = ["database", "native_api", "retained"]


@pytest.mark.parametrize(
    "retained",
    [
        (spatial, state)
        for spatial in (False, True)
        for state in ("populated", "empty", "all-null", "large")
    ],
    indirect=True,
)
def test_http_ordinary_readers(
    retained: Retained, database: Database, tmp_path: Path
) -> None:
    async def run() -> bytes:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=retained.public)
        ) as http:
            response = await http.get(retained.url, headers=mcp_headers())
            assert response.status_code == 200
            assert (
                response.headers["content-type"]
                == "application/vnd.apache.arrow.stream"
            )
            assert (await http.get(retained.url)).status_code == 401
            assert (
                await http.get(retained.url, headers=mcp_headers("wrong"))
            ).status_code == 403
            return response.content

    content = asyncio.run(run())
    for name in ("table.arrow", "table.parquet", "table.bin"):
        path = tmp_path / name
        path.write_bytes(content)
        with path.open("rb") as source, pa.ipc.open_stream(source) as reader:
            table = reader.read_all()
        assert table.column_names == list(retained.frame)
        assert all(field.nullable for field in table.schema)
        assert table.schema.field("ratio").metadata == {
            b"description": b"Declared ratio",
            b"unit": b"meter",
        }
        assert table.schema.field("count").type == pa.int64()
        if isinstance(retained.frame, gpd.GeoDataFrame):
            field = table.schema.field("geometry")
            assert field.type == pa.binary()
            assert field.metadata[b"ARROW:extension:name"] == b"geoarrow.wkb"
            restored = gpd.GeoDataFrame.from_arrow(table)
            assert_geodataframe_equal(restored, retained.frame)
            assert restored.geometry.name == "geometry"
            assert restored.crs is not None
            assert restored.crs.to_epsg() == 3857
        else:
            restored = table.to_pandas()
        pd.testing.assert_frame_equal(restored, retained.frame, check_exact=True)
    database.connection.__exit__.assert_called()
    database.engine.dispose.assert_called()
    # OpenAPI advertises the stream media type rather than a JSON wrapper.
    operation = retained.app.openapi()["paths"][
        "/results/{result_ref}/tables/{output_name}/download"
    ]["get"]
    assert (
        "application/vnd.apache.arrow.stream"
        in operation["responses"]["200"]["content"]
    )


@pytest.mark.usefixtures("database")
def test_mixed_groups_and_retained_ownership(tmp_path: Path) -> None:
    path = tmp_path / "results.sqlite"
    descriptions, reference = mixed_result(SQLiteStore(path))
    owner = descriptions.describe(reference).outputs["a"].owner_scope
    path.touch()
    service = ResultService(Mock(), path)
    table = pa.ipc.open_stream(
        b"".join(service.stream_table(reference, "a"))
    ).read_all()
    assert table.to_pydict() == {
        "location_id": ["same", "same", "next"],
        "row_key": ["a", "z", "r"],
        "value": [10, 30, 2],
    }
    assert not table.schema.field("location_id").nullable
    assert service.read_description(reference).outputs["a"].owner_scope == owner


@pytest.mark.parametrize(
    "damage", ["invalid", "absent", "output", "store", "registration", "rows"]
)
def test_lookup_errors_before_stream_and_mcp(
    retained: Retained, database: Database, damage: str
) -> None:
    reference, output = retained.reference, "rows"
    if damage == "invalid":
        reference = "bad"
        error = InvalidResultReferenceError(reference, output)
    elif damage in {"absent", "output", "store"}:
        if damage == "absent":
            reference = "0" * 32
        elif damage == "output":
            output = "unknown"
        else:
            retained.service.result_store_path.unlink()
        error = ResultNotFoundError(reference, output)
    else:
        error = ResultIntegrityError(reference, output)
        if damage == "registration":
            database.rows["storage_registrations"].clear()
        else:
            next(
                rows for name, rows in database.rows.items() if name.startswith("data_")
            ).pop()

    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
        ):
            response = await http.get(
                f"{BASE}/results/{reference}/tables/{output}/download"
            )
            payload = error_payload(error)
            assert response.status_code == error_status(payload)
            assert response.json() == {"detail": payload.model_dump()}
            response = await http.post(
                BASE + "/mcp/",
                json=tool_call_payload(
                    "lyra_download_table",
                    {"result_ref": reference, "output_name": output},
                ),
            )
            result = response.json()["result"]
            if damage == "rows":
                assert not result["isError"]
                assert result["structuredContent"]["download_url"] == retained.url
            else:
                assert result["isError"]
                assert result["structuredContent"] == {"error": payload.model_dump()}

    asyncio.run(run())


@pytest.mark.parametrize(
    "retained", [(True, "empty"), (True, "populated")], indirect=True
)
def test_mcp_link_is_typed_bounded_and_independent_of_report(
    retained: Retained, database: Database
) -> None:
    execute = database.execute

    def without_report(statement: sa.Select, parameters: object = None) -> object:
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        if source.name == "final_results":
            assert "report" not in statement.selected_columns
        return execute(statement, parameters)

    database.connection.execute.side_effect = without_report

    async def run() -> None:
        app = retained.app.state.mcp_app
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained.public),
                headers=mcp_headers(),
            ) as http,
            streamable_http_client(BASE + "/mcp/", http_client=http) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tool = next(
                t
                for t in (await session.list_tools()).tools
                if t.name == "lyra_download_table"
            )
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint
            assert tool.inputSchema["required"] == ["result_ref", "output_name"]
            assert tool.inputSchema["additionalProperties"] is False
            arguments = {"result_ref": retained.reference, "output_name": "rows"}
            result = await session.call_tool(tool.name, arguments)
            assert not result.isError
            validate(result.structuredContent, tool.outputSchema)
            assert result.structuredContent == {
                **arguments,
                "download_url": retained.url,
            }
            assert isinstance(result.content[0], TextContent)
            assert json.loads(result.content[0].text) == result.structuredContent
            assert mcp_response_size(result, 10) <= MAX_RETRIEVAL_BYTES
            for invalid in (
                {},
                {**arguments, "extra": True},
                {**arguments, "output_name": 4},
            ):
                failed = await session.call_tool(tool.name, invalid)
                assert failed.isError
                assert failed.structuredContent is None
            database.connection.execute.side_effect = execute
            response = await http.get(retained.url)
            assert response.status_code == 200
            assert pa.ipc.open_stream(response.content).read_all().num_rows == len(
                retained.frame
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "ending",
    ["complete", "disconnect", "receive-disconnect", "fetch-error", "conversion-error"],
)
def test_stream_resources_close(
    retained: Retained, database: Database, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    original = pa.ipc.new_stream
    released = []
    cursors = []

    @contextmanager
    def writer(*args: object) -> Generator[pa.RecordBatchStreamWriter]:
        with original(*args) as value:
            try:
                yield value
            finally:
                released.append("writer")

    monkeypatch.setattr(pa.ipc, "new_stream", writer)

    def execute(statement: sa.Select, parameters: object = None) -> object:
        cursor = database.execute(statement, parameters)
        if "LIMIT" in str(statement):
            cursors.append(cursor)
            if ending == "fetch-error":
                cursor.mappings.side_effect = RuntimeError("fetch failed")
        return cursor

    database.connection.execute.side_effect = execute
    if ending == "conversion-error":
        row = next(
            rows for name, rows in database.rows.items() if name.startswith("data_")
        )[0]
        row["ratio"] = float("inf")
    database.connection.reset_mock()
    database.engine.reset_mock()
    response = TableResponse(retained.service.stream_table(retained.reference, "rows"))
    sent = asyncio.Event()
    events = []

    async def receive() -> Message:
        await sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        await asyncio.sleep(0)
        events.append(message)
        sent.set()
        if ending == "disconnect" and message["type"] == "http.response.body":
            msg = "client disconnected"
            raise OSError(msg)

    async def run() -> None:
        version = "2.0" if ending == "receive-disconnect" else "2.4"
        await response(
            {"type": "http", "asgi": {"spec_version": version}}, receive, send
        )

    exception = {
        "complete": None,
        "receive-disconnect": None,
        "disconnect": ClientDisconnect,
        "fetch-error": RuntimeError,
        "conversion-error": None,
    }[ending]
    with nullcontext() if exception is None else pytest.raises(exception):
        asyncio.run(run())
    assert [
        event["status"] for event in events if event["type"] == "http.response.start"
    ] == {"fetch-error": [], "conversion-error": [500]}.get(ending, [200])
    assert released == ["writer"]
    assert all(cursor.close.called for cursor in cursors)
    assert next(response.stream, None) is None
    assert asyncio.run(anext(response.iterator, None)) is None
    database.connection.__exit__.assert_called()
    database.engine.dispose.assert_called()


@pytest.mark.parametrize("stage", ["preflight", "batch"])
def test_native_task_cancellation_joins_reader(
    retained: Retained, database: Database, stage: str
) -> None:
    release = threading.Event()
    response = TableResponse(retained.service.stream_table(retained.reference, "rows"))
    database.connection.reset_mock()
    database.engine.reset_mock()

    async def run() -> None:
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def execute(statement: sa.Select, parameters: object = None) -> object:
            if (stage == "batch" and "LIMIT" in str(statement)) or (
                stage == "preflight" and "FROM final_results" in str(statement)
            ):
                loop.call_soon_threadsafe(started.set)
                assert release.wait(timeout=5)
            return database.execute(statement, parameters)

        database.connection.execute.side_effect = execute

        async def receive() -> Message:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(_message: Message) -> None:
            await asyncio.sleep(0)

        task = asyncio.create_task(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        # Cancellation must not close a generator while the worker is inside it.
        await asyncio.sleep(0)
        try:
            assert not task.done()
            database.engine.dispose.assert_not_called()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert next(response.stream, None) is None
    database.connection.__exit__.assert_called()
    database.engine.dispose.assert_called()


@pytest.mark.parametrize("damaged", [0, -1])
@pytest.mark.parametrize("retained", [(True, "large")], indirect=True)
@pytest.mark.anyio
async def test_mcp_download_link_defers_cell_integrity_to_get(
    retained: Retained, database: Database, damaged: int
) -> None:
    rows = next(
        rows for name, rows in database.rows.items() if name.startswith("data_")
    )
    rows[damaged]["geometry"] = b"damaged"
    app = retained.app.state.mcp_app
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=retained.public)) as http,
    ):
        database.connection.execute.reset_mock()
        response = await http.post(
            BASE + "/mcp/",
            json=tool_call_payload(
                "lyra_download_table",
                {"result_ref": retained.reference, "output_name": "rows"},
            ),
            headers=mcp_headers(),
        )
        result = response.json()["result"]
        assert not result["isError"]
        assert all(
            not source.name.startswith("data_")
            for call in database.connection.execute.call_args_list
            for source in call.args[0].get_final_froms()
        )
        download = await http.get(
            result["structuredContent"]["download_url"], headers=mcp_headers()
        )
    assert download.status_code == 500
    expected = error_payload(ResultIntegrityError(retained.reference, "rows"))
    assert download.json() == {"detail": expected.model_dump()}


@pytest.mark.parametrize("damaged", [0, -1])
@pytest.mark.parametrize("retained", [(True, "large")], indirect=True)
def test_damaged_geometry_returns_json_and_preserves_destination(
    retained: Retained,
    database: Database,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
    damaged: int,
) -> None:
    rows = next(
        rows for name, rows in database.rows.items() if name.startswith("data_")
    )
    rows[damaged]["geometry"] = b"damaged"
    path = retained.service.result_store_path.with_suffix(".arrow")
    path.write_bytes(b"previous download")
    with (
        native_api.serve(retained.public, monkeypatch) as http,
        native_api.connect() as api,
    ):
        response = native_api.runner.run(http.get(retained.url, headers=mcp_headers()))
        assert response.status_code == 500
        expected = ResultIntegrityError(retained.reference, "rows")
        assert response.json() == {"detail": error_payload(expected).model_dump()}
        with pytest.raises(ResultIntegrityError):
            native_api.resolve(
                api.results.download_table(retained.reference, "rows", path)
            )
    assert path.read_bytes() == b"previous download"
