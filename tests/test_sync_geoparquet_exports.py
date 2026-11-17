"""Remote exports use retained Arrow values and complete descriptions."""

import asyncio
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pytest
from geopandas.testing import assert_geodataframe_equal

from lyra.api import LyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import ResultNotFoundError
from lyra.contracts.results import ResultDescriptionPreview
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.results import ResultService
from test_support.publication import publish_result
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
                        result_ref=reference,
                        execution_id=description.execution_id,
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
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(respond))
    )
    local = tmp_path / "local.parquet"
    getattr(reader, method)(reference, "a", local)
    remote = tmp_path / "remote.parquet"
    remote.write_bytes(b"old")
    with LyraClient("example.test/api") as client:
        assert (
            getattr(client.results, method)(
                reference, "a", str(remote) if preview else remote
            )
            is None
        )
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
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(respond))
    )
    with (
        LyraClient("example.test/api") as client,
        pytest.raises(ResultNotFoundError if failure == "lookup" else DownloadError),
    ):
        getattr(client.results, method)("a" * 32, "missing", destination)
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

    def run() -> None:
        with LyraClient("example.test/api") as client:
            monkeypatch.setattr(
                client.results,
                "describe",
                Mock(return_value=reader.describe(reference)),
            )
            name = "location_input" if input_export else "table"
            frame = (
                reader.read_location_input(reference, "a")
                if input_export
                else reader.read_table(reference, "a")
            )
            monkeypatch.setattr(client.results, name, Mock(return_value=frame))
            export = (
                client.results.export_location_input_geoparquet
                if input_export
                else client.results.export_geoparquet
            )
            with pytest.raises(type(error)) as caught:
                export(reference, "a", destination)
            if failure != "parent":
                assert caught.value is error

    run()
    destination = tmp_path / "export.parquet"
    assert set(tmp_path.iterdir()) == ({destination} if existing else set())
    if existing:
        assert destination.read_bytes() == b"original"
