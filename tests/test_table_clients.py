"""Native retained frames and atomic downloads through both public clients."""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import geopandas as gpd
import httpx
import pandas as pd
import pyarrow as pa
import pytest
from pydantic import ValidationError
from shapely.geometry import Point
from typing_extensions import override

from lyra.api import AsyncLyraClient
from lyra.api.exceptions import DownloadError
from lyra.contracts.errors import (
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.contracts.retrieval_paths import table_download_path
from tests.arrow_download_helpers import Retained, retained
from tests.httpx_download_helpers import ChunkStream, destinations
from tests.sqlite_helpers import database
from tests.table_client_helpers import ClientHarness, arrow_response, client

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from typing import IO

__all__ = ["client", "database", "destinations", "retained"]
_ID = "a" * 32
_MEDIA = "application/vnd.apache.arrow.stream"


@pytest.fixture(params=["download_table", "download_location_input"])
def download_client(
    request: pytest.FixtureRequest, client: ClientHarness
) -> tuple[ClientHarness, Callable[..., Awaitable[None] | None]]:
    return client, getattr(client.client.results, request.param)


@pytest.fixture(params=[False, True])
def preserved_destination(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Path]:
    path = tmp_path / "out"
    if request.param:
        path.write_bytes(b"original")
    yield path
    assert path.read_bytes() == b"original" if request.param else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if request.param else [])


@pytest.fixture(
    params=[
        (name, existing)
        for name in ("table.arrow", "table.parquet", "arbitrary")
        for existing in (False, True)
    ]
)
def download_path(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    directory = tmp_path / "downloads"
    directory.mkdir()
    name, existing = request.param
    path = directory / name
    if existing:
        path.write_bytes(b"original")
    return path


@pytest.mark.parametrize(
    "retained",
    [
        (spatial, state)
        for spatial in (False, True)
        for state in ("populated", "empty", "all-null", "large")
    ],
    indirect=True,
)
def test_native_table_values_and_isolation(
    retained: Retained, client: ClientHarness
) -> None:
    reference = retained.reference
    body = b"".join(retained.service.stream_table(reference, "rows"))
    description = retained.service.read_description(reference)
    client.responses.append(httpx.Response(200, json=description.model_dump()))
    captured = client.resolve(client.client.results.describe(reference))
    client.responses.extend(arrow_response(body) for _ in range(3))
    first = client.resolve(client.client.results.table(reference, "rows"))
    second = client.resolve(client.client.results.table(reference, "rows"))
    pd.testing.assert_frame_equal(first, retained.frame, check_exact=True)
    if isinstance(first, gpd.GeoDataFrame):
        assert first.geometry.name == "geometry"
        assert first.crs is not None
        assert first.crs.to_epsg() == 3857
        if len(first):
            # Missing and empty geometry, and the original projected coordinates,
            # must survive without GeoJSON reprojection or lossy conversion.
            assert (
                first.geometry.to_wkb().tolist()
                == retained.frame.geometry.to_wkb().tolist()
            )
            first.loc[0, "geometry"] = Point(1, 2)
        first.set_crs(4326, allow_override=True, inplace=True)
    if len(first):
        first.loc[0, "count"] = 12
        first.loc[0, "label"] = "changed"
    first.attrs["owner_scope"] = {"provider_id": "forged"}
    del first["flag"]
    third = client.resolve(client.client.results.table(reference, "rows"))
    client.close()
    for frame in (second, third):
        pd.testing.assert_frame_equal(frame, retained.frame, check_exact=True)
        assert "owner_scope" not in frame.attrs
    assert captured == description == retained.service.read_description(reference)
    with pytest.raises(RuntimeError, match="closed"):
        client.resolve(client.client.results.table(reference, "rows"))


@pytest.mark.parametrize(
    "retained",
    [(False, "populated"), (True, "empty"), (True, "populated")],
    indirect=True,
)
def test_public_download_preserves_bytes_and_reads_normally(
    retained: Retained,
    download_client: tuple[ClientHarness, Callable[..., Awaitable[None] | None]],
    destinations: list[IO[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    download_path: Path,
) -> None:
    client, download = download_client
    path = download_path
    directory = path.parent
    existing = path.exists()
    body = b"".join(retained.service.stream_table(retained.reference, "rows"))

    def observe(consumed: int) -> None:
        # File downloads must use bounded memory: persist each chunk before
        # consuming the next one, leaving the destination untouched until done.
        assert destinations[0].tell() == consumed
        assert path.read_bytes() == b"original" if existing else not path.exists()

    stream = ChunkStream([body[:100], body[100:]], observe)
    response = httpx.Response(200, stream=stream, headers={"Content-Type": _MEDIA})
    client.responses.append(response)
    replace = Path.replace

    def after_close(source: Path, target: str | Path) -> Path:
        assert stream.closed
        assert response.is_closed
        assert destinations[0].closed
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", after_close)
    assert client.resolve(download(retained.reference, "rows", path)) is None
    assert path.read_bytes() == body
    assert list(directory.iterdir()) == [path]
    with path.open("rb") as source, pa.ipc.open_stream(source) as reader:
        table = reader.read_all()
    frame = (
        gpd.GeoDataFrame.from_arrow(table)
        if isinstance(retained.frame, gpd.GeoDataFrame)
        else table.to_pandas()
    )
    pd.testing.assert_frame_equal(frame, retained.frame, check_exact=True)


@pytest.mark.parametrize("method", ["table", "download_table"])
@pytest.mark.parametrize(
    "error",
    [
        ResultNotFoundError(_ID, "rows"),
        ResultIntegrityError(_ID, "rows"),
    ],
)
def test_structured_errors_precede_arrow_decoding(
    client: ClientHarness, tmp_path: Path, method: str, error: ResultAccessError
) -> None:
    payload = error_payload(error)
    response = httpx.Response(
        error_status(payload), json={"detail": payload.model_dump()}
    )
    client.responses.append(response)
    path = tmp_path / "out"
    path.write_bytes(b"original")
    with pytest.raises(type(error)) as caught:
        client.resolve(
            client.client.results.table(_ID, "rows")
            if method == "table"
            else client.client.results.download_table(_ID, "rows", path)
        )
    assert vars(caught.value) == vars(error)
    assert response.is_closed
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("method", ["table", "download_table"])
@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/vnd.apache.arrow.file", "", _MEDIA + "-wrong"],
)
def test_wrong_media_type_rejected(
    client: ClientHarness, tmp_path: Path, method: str, content_type: str
) -> None:
    stream = ChunkStream([b"unexpected"])
    response = httpx.Response(
        200, stream=stream, headers={"Content-Type": content_type}
    )
    client.responses.append(response)
    with pytest.raises(
        DownloadError, match=r"expected application/vnd\.apache\.arrow\.stream"
    ):
        client.resolve(
            client.client.results.table(_ID, "rows")
            if method == "table"
            else client.client.results.download_table(_ID, "rows", tmp_path / "out")
        )
    assert stream.closed
    assert response.is_closed
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "damage", ["empty", "garbage", "truncated", "metadata", "geometry"]
)
def test_malformed_arrow_fails_without_partial_frame(
    retained: Retained, client: ClientHarness, damage: str
) -> None:
    body = b"".join(retained.service.stream_table(retained.reference, "rows"))
    if damage == "empty":
        body = b""
    elif damage == "garbage":
        body = b"not an IPC stream"
    elif damage == "truncated":
        body = body[:-16]
    else:
        table = pa.ipc.open_stream(body).read_all()
        if damage == "metadata":
            table = table.replace_schema_metadata({b"pandas": b"{"})
        else:
            table = table.set_column(
                table.schema.get_field_index("geometry"),
                table.schema.field("geometry"),
                pa.array([b"bad"] * table.num_rows),
            )
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        body = sink.getvalue().to_pybytes()
    response = arrow_response(body)
    client.responses.append(response)
    with pytest.raises(DownloadError, match="invalid Arrow response"):
        client.resolve(client.client.results.table(retained.reference, "rows"))
    assert response.is_closed


@pytest.mark.parametrize("method", ["table", "download_table"])
def test_encoded_output_path_and_invalid_reference(
    client: ClientHarness, tmp_path: Path, method: str
) -> None:
    path = tmp_path / "out"
    output = "雪 /%?#"
    with pytest.raises(ValidationError):
        client.resolve(
            client.client.results.table("bad", output)
            if method == "table"
            else client.client.results.download_table("bad", output, path)
        )
    assert not client.requests
    assert not path.exists()
    client.responses.append(httpx.Response(404, text="missing"))
    with pytest.raises(DownloadError):
        client.resolve(
            client.client.results.table(_ID, output)
            if method == "table"
            else client.client.results.download_table(_ID, output, path)
        )
    assert str(
        client.requests[0].url
    ) == "https://example.test/api/" + table_download_path(_ID, output)


def break_file_operation(
    file: IO[bytes], stage: str, failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    if stage == "cancel_write":
        write = file.write

        def cancelled_write(data: bytes) -> int:
            write(data[:2])
            raise asyncio.CancelledError

        monkeypatch.setattr(file, "write", cancelled_write)
    elif stage == "write":

        def failing_write(_data: bytes) -> int:
            raise failure

        monkeypatch.setattr(file, "write", failing_write)
    elif stage == "file_close":
        close = file.close

        def failing_close() -> None:
            close()
            raise failure

        monkeypatch.setattr(file, "close", failing_close)


@pytest.mark.parametrize(
    "stage",
    [
        "stream",
        "write",
        "file_close",
        "response_close",
        "replace",
        "interrupt",
        "cancel_write",
    ],
)
def test_download_failure_preserves_destination(
    download_client: tuple[ClientHarness, Callable[..., Awaitable[None] | None]],
    preserved_destination: Path,
    destinations: list[IO[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    client, download = download_client
    path = preserved_destination
    failure = httpx.ReadTimeout("stalled") if stage == "stream" else OSError(stage)

    class FailingStream(ChunkStream):
        @override
        def close(self) -> None:
            super().close()
            if stage == "response_close":
                raise failure

    def fail(*_args: object) -> None:
        raise failure

    def observe(consumed: int) -> None:
        if consumed and stage == "interrupt":
            raise KeyboardInterrupt
        if consumed == 0:
            break_file_operation(destinations[0], stage, failure, monkeypatch)

    stream = FailingStream(
        [b"partial"], observe, failure if stage == "stream" else None
    )
    response = httpx.Response(200, stream=stream, headers={"Content-Type": _MEDIA})
    client.responses.append(response)
    if stage == "replace":
        monkeypatch.setattr(Path, "replace", fail)
    expected = (
        asyncio.CancelledError
        if stage == "cancel_write"
        else KeyboardInterrupt
        if stage == "interrupt"
        else DownloadError
        if stage == "stream"
        else OSError
    )
    with pytest.raises(expected):
        client.resolve(download(_ID, "rows", path))
    assert stream.closed
    assert response.is_closed
    assert destinations[0].closed


def test_download_requires_existing_parent(
    download_client: tuple[ClientHarness, Callable[..., Awaitable[None] | None]],
    tmp_path: Path,
) -> None:
    client, download = download_client
    response = arrow_response(b"body")
    client.responses.append(response)
    with pytest.raises(FileNotFoundError):
        client.resolve(download(_ID, "rows", tmp_path / "missing" / "out"))
    assert response.is_closed
    assert not list(tmp_path.iterdir())


def test_table_stream_transport_failure(client: ClientHarness) -> None:
    failure = httpx.ReadTimeout("stalled")
    stream = ChunkStream([b"partial"], failure=failure)
    response = httpx.Response(200, stream=stream, headers={"Content-Type": _MEDIA})
    client.responses.append(response)
    with pytest.raises(DownloadError) as caught:
        client.resolve(client.client.results.table(_ID, "rows"))
    assert caught.value.__cause__ is failure
    assert caught.value.retryable
    assert stream.closed
    assert response.is_closed


def test_async_table_through_http_endpoint(
    retained: Retained, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.ASGITransport(app=retained.public)),
    )

    async def run() -> None:
        async with AsyncLyraClient(
            "lyra.example.test/public/api", agent_api_key="agent-secret"
        ) as native:
            frame = await native.results.table(retained.reference, "rows")
            await native.results.download_table(
                retained.reference, "rows", tmp_path / "out"
            )
        pd.testing.assert_frame_equal(frame, retained.frame, check_exact=True)
        with pa.ipc.open_stream(tmp_path / "out") as reader:
            restored = gpd.GeoDataFrame.from_arrow(reader.read_all())
        pd.testing.assert_frame_equal(restored, frame, check_exact=True)

    asyncio.run(run())
