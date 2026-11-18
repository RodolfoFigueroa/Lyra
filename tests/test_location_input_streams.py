"""Input Arrow fidelity, bounded reads, preflight and resource ownership."""

import asyncio
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
import sqlalchemy as sa
from starlette.requests import ClientDisconnect
from starlette.types import Message

from lyra.contracts.errors import (
    ResultIntegrityError,
    ResultNotFoundError,
    RowTooLargeError,
)
from lyra.runtime.result_store import ResultStore
from lyra.server import table_response
from lyra.server.response_budget import http_response_budget
from lyra.server.result_rows import RowPageBuilder, RowPageContext
from lyra.server.results import ResultService
from lyra.server.table_response import TableResponse
from tests.location_input_arrow_helpers import RetainedInput, retained_input
from tests.sqlite_helpers import Database, database

__all__ = ["database", "retained_input"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize(
    "retained_input", ["populated", "all-null", "empty", "large"], indirect=True
)
def test_arrow_types_geometry_and_multiple_batches(
    retained_input: RetainedInput, database: Database
) -> None:
    retained = retained_input
    reader = ResultStore(retained.store)
    cursors = []

    def execute(statement: object, parameters: object = None) -> object:
        result = database.execute(statement, parameters)
        if isinstance(statement, sa.sql.Select) and "FROM data_" in str(statement):
            # No value query may grow with the complete requested input.
            params = statement.compile().params
            assert "LIMIT" in str(statement)
            assert int(list(params.values())[-1]) <= 1025
            cursors.append(result)
        return result

    database.connection.execute.side_effect = execute
    stream = reader.stream_location_input(retained.reference, "places")
    first = next(stream)
    assert not cursors  # Preflight emits schema without loading geometry.
    with pa.ipc.open_stream(first + b"".join(stream)) as ipc:
        table = ipc.read_all()
    assert all(cursor.close.called for cursor in cursors)
    assert (
        table.schema.field("geometry").metadata[b"ARROW:extension:name"]
        == b"geoarrow.wkb"
    )
    assert table.schema.field("ratio").metadata[b"unit"] == b"meter"
    assert table.schema.field("count").nullable
    assert not table.schema.field("location_id").nullable
    native = gpd.GeoDataFrame.from_arrow(table)
    pd.testing.assert_frame_equal(native, retained.frame, check_exact=True)
    pd.testing.assert_frame_equal(
        reader.read_location_input(retained.reference, "places"),
        native,
        check_exact=True,
    )
    if len(native) > 1024:
        # Exercise continuation across both target fetch and Arrow batch boundaries.
        builder = RowPageBuilder(reader, "https://lyra.test", http_response_budget())
        rows = []
        cursor = None
        while True:
            page = builder.build_location_input(
                retained.reference, "places", 600, cursor
            )
            rows.extend(page.rows)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert [row["location_id"] for row in rows] == native.location_id.tolist()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ratio", "NaN"),
        ("ratio", float("inf")),
        ("count", 1.5),
        ("label", b"text"),
        ("flag", 2),
        ("geometry", None),
        ("geometry", b"010100000000000000000000000000000000000000"),
    ],
)
def test_corrupt_input_cells(
    retained_input: RetainedInput, database: Database, column: str, value: object
) -> None:
    retained = retained_input
    table = retained.store.layout.tables[retained.selection.registration.key].table
    database.rows[table.name][0][column] = value
    reader = ResultStore(retained.store)
    builder = RowPageBuilder(reader, "https://lyra.test", http_response_budget())
    for read in (
        lambda: reader.read_location_input(retained.reference, "places"),
        lambda: builder.build_location_input(retained.reference, "places"),
        lambda: b"".join(retained.stream()),
    ):
        with pytest.raises(ResultIntegrityError) as error:
            read()
        assert error.value.input_name == "places"


def test_json_logical_encodings(retained_input: RetainedInput) -> None:
    retained = retained_input
    builder = RowPageBuilder(
        ResultStore(retained.store), "https://lyra.test", http_response_budget()
    )
    rows = builder.build_location_input(retained.reference, "places").rows
    assert [row["count"] for row in rows] == [str(-(2**63)), str(2**63 - 1), "12", None]
    assert [row["flag"] for row in rows] == [True, False, True, None]
    assert [row["ratio"] for row in rows] == [1.2345678901234567, -2.5, 0.0, None]
    assert rows[0]["location_id"] == "0012"
    assert rows[0]["geometry"] == "POINT (1234.123456789 -4567.987654321)"
    assert all(set(row) == set(retained.frame.columns) for row in rows)


@pytest.mark.parametrize(
    "retained_input", ["oversized-first", "oversized-later"], indirect=True
)
def test_oversized_input_remains_downloadable(retained_input: RetainedInput) -> None:
    retained = retained_input
    builder = RowPageBuilder(
        ResultStore(retained.store), "https://lyra.test", http_response_budget()
    )
    cursor = None
    if len(retained.frame.label.iloc[0]) < 1000:
        page = builder.build_location_input(retained.reference, "places")
        assert len(page.rows) == 2
        cursor = page.next_cursor
        assert cursor is not None
    for _ in range(2):
        with pytest.raises(RowTooLargeError):
            builder.build_location_input(retained.reference, "places", cursor=cursor)
    native = gpd.GeoDataFrame.from_arrow(
        pa.ipc.open_stream(b"".join(retained.stream())).read_all()
    )
    pd.testing.assert_frame_equal(native, retained.frame)


@pytest.mark.parametrize(
    "ending", ["complete", "close", "fetch-error", "encoding-error"]
)
def test_stream_cleanup(
    retained_input: RetainedInput,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
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

    def execute(statement: object, parameters: object = None) -> object:
        result = database.execute(statement, parameters)
        if "LIMIT" in str(statement):
            cursors.append(result)
            if ending == "fetch-error":
                result.mappings.side_effect = RuntimeError("fetch failed")
        return result

    database.connection.execute.side_effect = execute
    if ending == "encoding-error":
        data = retained_input.store.layout.tables[
            retained_input.selection.registration.key
        ]
        database.rows[data.table.name][0]["ratio"] = float("inf")
    database.connection.reset_mock()
    stream = retained_input.stream()
    next(stream)
    error = {"fetch-error": RuntimeError, "encoding-error": ResultIntegrityError}.get(
        ending
    )
    with nullcontext() if error is None else pytest.raises(error):
        if ending == "close":
            next(stream)
            stream.close()
        else:
            list(stream)
    assert released == ["writer"]
    assert all(cursor.close.called for cursor in cursors)
    assert next(stream, None) is None
    database.connection.__exit__.assert_called()
    database.engine.dispose.assert_called()


@pytest.mark.parametrize("stage", ["preflight", "batch"])
def test_input_response_cancellation_joins_reader(
    retained_input: RetainedInput, database: Database, stage: str
) -> None:
    response = TableResponse(retained_input.stream())
    release = threading.Event()
    database.connection.reset_mock()
    database.engine.reset_mock()

    async def run() -> None:
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def execute(statement: object, parameters: object = None) -> object:
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


def test_shared_service_pages_and_missing_store(
    retained_input: RetainedInput, tmp_path: Path
) -> None:
    retained = retained_input
    jobs = Mock(spec=[])
    service = ResultService(jobs, retained.store.path)
    context = RowPageContext("https://lyra.test", http_response_budget())
    service.validate_location_input(retained.reference, "places")
    first = service.read_location_input_rows(
        retained.reference, "places", 1, context=context
    )
    second = asyncio.run(
        service.location_input_rows(
            retained.reference, "places", 1, first.next_cursor, context=context
        )
    )
    assert [first.rows[0]["location_id"], second.rows[0]["location_id"]] == [
        "0012",
        "002",
    ]
    jobs.assert_not_called()
    missing = ResultService(jobs, tmp_path / "missing.sqlite")
    for call in (
        lambda: missing.read_location_input_rows(
            retained.reference, "places", context=context
        ),
        lambda: missing.validate_location_input(retained.reference, "places"),
        lambda: next(missing.stream_location_input(retained.reference, "places")),
    ):
        with pytest.raises(ResultNotFoundError) as caught:
            call()
        assert caught.value.result_ref == retained.reference
        assert caught.value.input_name == "places"
        assert caught.value.output_name is None


@pytest.mark.parametrize(
    ("damaged", "corruption"), [(0, "missing"), (-1, "missing"), (-1, "wkb")]
)
@pytest.mark.parametrize("retained_input", ["large"], indirect=True)
def test_damaged_record_returns_integrity_error_before_arrow(
    retained_input: RetainedInput,
    database: Database,
    damaged: int,
    corruption: str,
) -> None:
    retained = retained_input
    data = retained.store.layout.tables[retained.selection.registration.key]
    if corruption == "missing":
        database.rows[data.table.name].pop(damaged)
    else:
        database.rows[data.table.name][damaged]["geometry"] = b"damaged"
    events = []

    async def send(message: Message) -> None:
        events.append(message)
        await asyncio.sleep(0)

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    asyncio.run(
        TableResponse(retained.stream())(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
    )
    assert events[0]["status"] == 500
    body = json.loads(events[1]["body"])["detail"]
    assert body["code"] == "result_integrity_error"
    assert body["input_name"] == "places"


@pytest.mark.parametrize("phase", ["preparation", "transmission"])
def test_verified_download_disconnect_closes_temporary_file(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    files = []
    closed = threading.Event()
    disconnect = threading.Event()

    @contextmanager
    def temporary_file() -> Generator[BinaryIO]:
        with TemporaryFile() as staged:
            files.append(staged)
            yield staged

    def chunks() -> Generator[bytes]:
        try:
            yield b"schema"
            if phase == "preparation":
                assert disconnect.wait(timeout=5)
            yield b"batch"
        finally:
            closed.set()

    monkeypatch.setattr(table_response, "TemporaryFile", temporary_file)
    messages = []

    async def receive() -> Message:
        if phase == "transmission":
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        disconnect.set()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        await asyncio.sleep(0)
        messages.append(message["type"])
        if message["type"] == "http.response.body":
            message_text = "disconnected"
            raise OSError(message_text)

    async def run() -> None:
        response = TableResponse(chunks())
        with (
            pytest.raises(ClientDisconnect)
            if phase == "transmission"
            else nullcontext()
        ):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )

    asyncio.run(run())
    assert closed.is_set()
    assert all(file.closed for file in files)
    if phase == "preparation":
        assert messages == []
    else:
        assert "http.response.start" in messages
