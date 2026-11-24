"""Input Arrow fidelity, bounded reads, preflight and resource ownership."""

import asyncio
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
import sqlalchemy as sa
from starlette.types import Message

from lyra.contracts.errors import (
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.runtime.result_store import ResultStore
from lyra.server import table_response
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


@pytest.mark.parametrize(
    "value", [b"010100000000000000000000000000000000000000", b"\x01broken"]
)
def test_corrupt_input_geometry(
    retained_input: RetainedInput, database: Database, value: bytes
) -> None:
    retained = retained_input
    table = retained.store.layout.tables[retained.selection.registration.key].table
    database.rows[table.name][0]["geometry"] = value
    reader = ResultStore(retained.store)
    for read in (
        lambda: reader.read_location_input(retained.reference, "places"),
        lambda: b"".join(retained.stream()),
    ):
        with pytest.raises(ResultIntegrityError) as error:
            read()
        assert error.value.input_name == "places"


@pytest.mark.parametrize(
    "retained_input", ["oversized-first", "oversized-later"], indirect=True
)
def test_oversized_input_remains_downloadable(retained_input: RetainedInput) -> None:
    retained = retained_input
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
        database.rows[data.table.name][0]["geometry"] = b"\x01truncated"
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


def test_input_lookup_and_missing_store(
    retained_input: RetainedInput, tmp_path: Path
) -> None:
    retained = retained_input
    jobs = Mock(spec=[])
    service = ResultService(jobs, retained.store.path)
    service.validate_location_input(retained.reference, "places")
    jobs.assert_not_called()
    missing = ResultService(jobs, tmp_path / "missing.sqlite")
    for call in (
        lambda: missing.validate_location_input(retained.reference, "places"),
        lambda: next(missing.stream_location_input(retained.reference, "places")),
    ):
        with pytest.raises(ResultNotFoundError) as caught:
            call()
        assert caught.value.job_id == retained.reference
        assert caught.value.input_name == "places"
        assert caught.value.output_name is None


@pytest.mark.parametrize(
    ("damaged", "corruption"),
    [(0, "missing"), (-1, "missing"), (-1, "wkb"), (-1, "trailing")],
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
    elif corruption == "trailing":
        database.rows[data.table.name][damaged]["geometry"] += b"junk"
    else:
        database.rows[data.table.name][damaged]["geometry"] = b"\x01truncated"
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


@pytest.mark.parametrize(
    "phase", ["success", "staging-error", "transmission-disconnect"]
)
def test_staged_file_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    monkeypatch.setattr(
        table_response, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path)
    )
    closed = threading.Event()
    messages = []

    def chunks() -> Generator[bytes]:
        try:
            yield b"schema"
            if phase == "staging-error":
                raise ResultIntegrityError("a" * 32, "rows")
            yield b"batch"
        finally:
            closed.set()

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        await asyncio.sleep(0)
        assert closed.is_set()
        if message["type"] == "http.response.start":
            messages.append(message["status"])
        if (
            phase == "transmission-disconnect"
            and message["type"] == "http.response.body"
        ):
            msg = "disconnect"
            raise OSError(msg)

    with (
        pytest.raises(OSError, match="disconnect")
        if phase == "transmission-disconnect"
        else nullcontext()
    ):
        asyncio.run(
            TableResponse(chunks())(
                {
                    "type": "http",
                    "method": "GET",
                    "headers": [],
                    "asgi": {"spec_version": "2.4"},
                },
                receive,
                send,
            )
        )
    assert messages == [500 if phase == "staging-error" else 200]
    assert not list(tmp_path.iterdir())
