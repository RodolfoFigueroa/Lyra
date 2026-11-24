"""Remote exports use retained Arrow values and complete descriptions."""

import asyncio
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import geopandas as gpd
import httpx
import pytest
from geopandas.testing import assert_geodataframe_equal

from lyra.api import AsyncLyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import ResultNotFoundError
from lyra.contracts.results import ResultDescriptionPreview
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.results import ResultService
from test_support.publication import publish_result
from tests.async_cancellation_helpers import (
    CancellableStream,
    CancellationHarness,
)
from tests.export_lifecycle_helpers import inject_failure, observe_publication
from tests.location_input_helpers import publish_inputs, retained
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import database
from tests.table_client_helpers import arrow_response

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("selection", ["output", "provider", "correspondence"])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("preview", [False, True])
def test_remote_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    *,
    empty: bool,
    preview: bool,
) -> None:
    store = SQLiteStore(tmp_path / "store")
    if selection == "output":
        value = publish_values(store, () if empty else ("one", "two"), spatial=True)
        reference = publish_result(ResultDescriptions(store), value)
        method = "export_geoparquet"
    else:
        provider, correspondence = retained(store)
        value = provider if selection == "provider" else correspondence
        if empty:
            value = value.model_copy(update={"records": ()})
        reference = publish_inputs(
            store, {"a": value}, output_keys=() if empty else ("one", "two")
        )
        method = "export_location_input_geoparquet"
    store.path.touch()
    reader = ResultStore(store)
    description = reader.describe(reference)
    selected = (
        description.outputs if selection == "output" else description.location_inputs
    )["a"]
    if preview:
        selected.table_schema.columns[0].description = (
            "Large retained description " * 10000
        )
    service = ResultService(Mock(spec=[]), store.path)
    stream = (
        service.stream_table if selection == "output" else service.stream_location_input
    )
    responses = [arrow_response(b"".join(stream(reference, "a")))]
    if preview:
        responses.extend(
            (
                httpx.Response(
                    200,
                    json=ResultDescriptionPreview(
                        job_id=reference,
                        download_url=f"https://example.test/api/results/{reference}/description/download",
                    ).model_dump(mode="json"),
                ),
                httpx.Response(200, json=description.model_dump(mode="json")),
            )
        )
    else:
        responses.append(
            httpx.Response(
                200,
                json={
                    "kind": "complete",
                    "description": description.model_dump(mode="json"),
                },
            )
        )

    served = list(responses)
    observe_publication(monkeypatch, served)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return responses.pop(0)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
    )
    local = tmp_path / "local.parquet"
    getattr(reader, method)(reference, "a", local)
    remote = tmp_path / "remote.parquet"
    remote.write_bytes(b"old")

    async def run() -> None:
        async with AsyncLyraClient("example.test/api") as client:
            assert (
                await getattr(client.results, method)(
                    reference, "a", str(remote) if preview else remote
                )
                is None
            )

    asyncio.run(run())
    assert all(response.is_closed for response in served)
    assert not responses
    assert_geodataframe_equal(gpd.read_parquet(remote), gpd.read_parquet(local))
    assert gpd.read_parquet(remote).attrs == {
        "lyra": {
            **gpd.read_parquet(local).attrs["lyra"],
            "description": selected.model_dump(mode="json"),
        }
    }


@pytest.mark.parametrize(
    "method", ["export_geoparquet", "export_location_input_geoparquet"]
)
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["lookup", "decode", "transport"])
def test_retrieval_failure_preserves_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    failure: str,
    *,
    existing: bool,
) -> None:

    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")
    error = ResultNotFoundError("a" * 32, "missing")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if failure == "transport":
            message = "interrupted"
            raise httpx.ReadError(message)
        if failure == "decode":
            return arrow_response(b"invalid arrow")
        payload = error_payload(error)
        return httpx.Response(
            error_status(payload), json={"detail": payload.model_dump()}
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
    )

    async def run() -> None:
        async with AsyncLyraClient("example.test/api") as client:
            with pytest.raises(
                ResultNotFoundError if failure == "lookup" else DownloadError
            ):
                await getattr(client.results, method)("a" * 32, "missing", destination)

    asyncio.run(run())
    assert list(tmp_path.iterdir()) == ([destination] if existing else [])
    if existing:
        assert destination.read_bytes() == b"original"


@pytest.mark.parametrize(
    "method", ["export_geoparquet", "export_location_input_geoparquet"]
)
@pytest.mark.parametrize("stage", ["arrow", "description", "preview"])
@pytest.mark.parametrize("existing", [False, True])
def test_cancelled_retrieval_closes_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    stage: str,
    *,
    existing: bool,
) -> None:

    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")

    async def run() -> None:
        harness = CancellationHarness("download", cleanup_fails=False)
        response = httpx.Response(
            200,
            stream=CancellableStream(harness),
            headers={"Content-Type": "application/vnd.apache.arrow.stream"},
        )
        calls = 0

        def respond(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if stage == "preview" and calls == 1:
                return httpx.Response(
                    200,
                    json=ResultDescriptionPreview(
                        job_id="a" * 32,
                        download_url="https://example.test/api/description/download",
                    ).model_dump(mode="json"),
                )
            return response

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
        )
        async with AsyncLyraClient("example.test/api") as client:
            if stage != "arrow":
                table = AsyncMock(return_value=None)

                monkeypatch.setattr(
                    client.results,
                    "table" if method == "export_geoparquet" else "location_input",
                    table,
                )
            task = asyncio.create_task(
                getattr(client.results, method)("a" * 32, "a", destination)
            )
            await harness.entered.wait()
            task.cancel()
            await harness.closing.wait()
            assert not task.done()
            harness.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert harness.cleaned
            assert response.is_closed
            assert all(owner is task for owner in harness.tasks)

    asyncio.run(run())
    assert list(tmp_path.iterdir()) == ([destination] if existing else [])
    if existing:
        assert destination.read_bytes() == b"original"


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("input_export", [False, True])
@pytest.mark.parametrize(
    "failure",
    ["conversion", "writer", "replace", "cancel", "denied", "close", "parent"],
)
def test_atomic_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    *,
    existing: bool,
    input_export: bool,
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    if input_export:
        direct, _ = retained(writer)
        reference = publish_inputs(writer, {"a": direct})
    else:
        reference = publish_result(
            ResultDescriptions(writer), publish_values(writer, ("one",), spatial=True)
        )
    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")
    error = (
        asyncio.CancelledError()
        if failure == "cancel"
        else PermissionError("denied")
        if failure == "denied"
        else ValueError("failure")
    )

    if failure == "parent":
        destination = tmp_path / "missing" / "export.parquet"
        error = FileNotFoundError()
    inject_failure(failure, error, monkeypatch)
    reader = ResultStore(writer)

    async def run() -> None:
        async with AsyncLyraClient("example.test/api") as client:
            monkeypatch.setattr(
                client.results,
                "describe",
                AsyncMock(return_value=reader.describe(reference)),
            )
            name = "location_input" if input_export else "table"
            frame = (
                reader.read_location_input(reference, "a")
                if input_export
                else reader.read_table(reference, "a")
            )
            monkeypatch.setattr(client.results, name, AsyncMock(return_value=frame))
            export = (
                client.results.export_location_input_geoparquet
                if input_export
                else client.results.export_geoparquet
            )
            with pytest.raises(type(error)) as caught:
                await export(reference, "a", destination)
            if failure != "parent":
                assert caught.value is error

    asyncio.run(run())
    destination = tmp_path / "export.parquet"
    assert set(tmp_path.iterdir()) == ({destination} if existing else set())
    if existing:
        assert destination.read_bytes() == b"original"
