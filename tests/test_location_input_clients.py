"""Retained input access through both native clients with isolated HTTP."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pandas as pd
import pytest
from pydantic import ValidationError

from lyra.contracts.errors import (
    InvalidPaginationError,
    LocationInputUnavailableError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
    RowTooLargeError,
)
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.contracts.retrieval_paths import location_input_download_path
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.response_budget import http_response_budget
from lyra.server.result_rows import RowPageContext
from lyra.server.results import ResultService
from tests.location_input_arrow_helpers import RetainedInput, retained_input
from tests.location_input_helpers import publish_inputs, retained
from tests.sqlite_helpers import database
from tests.table_client_helpers import ClientHarness, arrow_response, client

__all__ = ["client", "database", "retained_input"]
pytestmark = pytest.mark.usefixtures("database")
_ID = "a" * 32
_METHODS = ["location_input", "location_input_rows", "download_location_input"]


@pytest.mark.parametrize(
    "retained_input", ["populated", "empty", "all-null", "large"], indirect=True
)
def test_native_input_fidelity_and_isolation(
    retained_input: RetainedInput, client: ClientHarness
) -> None:
    body = b"".join(retained_input.stream())
    client.responses.extend(arrow_response(body) for _ in range(3))
    frames = [
        client.resolve(
            client.client.results.location_input(retained_input.reference, "places")
        )
        for _ in range(2)
    ]
    pd.testing.assert_frame_equal(frames[0], retained_input.frame, check_exact=True)
    del frames[0]["label"]
    frames[0].set_crs(4326, allow_override=True, inplace=True)
    frames.append(
        client.resolve(
            client.client.results.location_input(retained_input.reference, "places")
        )
    )
    client.close()
    for frame in frames[1:]:
        assert isinstance(frame, gpd.GeoDataFrame)
        pd.testing.assert_frame_equal(frame, retained_input.frame, check_exact=True)
    assert all(
        request.url.path.endswith("/location-inputs/places/download")
        for request in client.requests
    )
    with pytest.raises(RuntimeError, match="closed"):
        client.resolve(
            client.client.results.location_input(retained_input.reference, "places")
        )


@pytest.mark.parametrize("as_string", [False, True])
def test_correspondence_reads_pages_and_downloads(
    client: ClientHarness, tmp_path: Path, *, as_string: bool
) -> None:
    store = SQLiteStore(tmp_path / "store")
    _, pairs = retained(store)
    reference = publish_inputs(store, {"a": pairs}, output_keys=("one", "two"))
    store.path.touch()
    service = ResultService(Mock(spec=[]), store.path)
    expected = ResultStore(store).read_location_input(reference, "a")
    body = b"".join(service.stream_location_input(reference, "a"))
    client.responses.append(arrow_response(body))
    frame = client.resolve(client.client.results.location_input(reference, "a"))
    pd.testing.assert_frame_equal(frame, expected)
    assert frame.location_id.tolist() == ["a", "a"]
    assert set(frame.source_location_id) == {"A", "B"}
    cursor = None
    rows = []
    while True:
        page = service.read_location_input_rows(
            reference,
            "a",
            limit=1,
            cursor=cursor,
            context=RowPageContext("https://example.test/api", http_response_budget()),
        )
        client.responses.append(httpx.Response(200, json=page.model_dump()))
        actual = client.resolve(
            client.client.results.location_input_rows(
                reference, "a", limit=1, cursor=cursor
            )
        )
        assert actual == page
        assert client.requests[-1].url.params.get("cursor") == cursor
        assert client.requests[-1].url.params["limit"] == "1"
        rows.extend(actual.rows)
        cursor = actual.next_cursor
        if cursor is None:
            break
    assert len(rows) == 2
    output_page = service.read_rows(
        reference,
        "a",
        limit=1,
        context=RowPageContext("https://example.test/api", http_response_budget()),
    )
    with pytest.raises(InvalidPaginationError) as wrong:
        service.read_location_input_rows(
            reference,
            "a",
            cursor=output_page.next_cursor,
            context=RowPageContext("https://example.test/api", http_response_budget()),
        )
    payload = error_payload(wrong.value)
    client.responses.append(httpx.Response(422, json={"detail": payload.model_dump()}))
    with pytest.raises(InvalidPaginationError) as caught:
        client.resolve(
            client.client.results.location_input_rows(
                reference, "a", cursor=output_page.next_cursor
            )
        )
    assert vars(caught.value) == vars(wrong.value)
    path = tmp_path / "saved"
    path.write_bytes(b"original")
    client.responses.append(arrow_response(body))
    assert (
        client.resolve(
            client.client.results.download_location_input(
                reference, "a", destination=str(path) if as_string else path
            )
        )
        is None
    )
    assert path.read_bytes() == body


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize(
    "error",
    [
        LocationInputUnavailableError(_ID, "places"),
        ResultNotFoundError(_ID, input_name="places"),
        ResultIntegrityError(_ID, input_name="places"),
        InvalidPaginationError(_ID, None, "cursor", input_name="places"),
        InvalidPaginationError(_ID, None, "limit", input_name="places"),
        RowTooLargeError(
            _ID, None, "https://example.test/input/download", input_name="places"
        ),
    ],
)
def test_input_errors(
    client: ClientHarness, tmp_path: Path, method: str, error: ResultAccessError
) -> None:
    payload = error_payload(error).model_dump()
    payload["message"] = "Unrelated informational wording"
    response = httpx.Response(
        error_status(error_payload(error)), json={"detail": payload}
    )
    client.responses.append(response)
    path = tmp_path / "out"
    path.write_bytes(b"original")
    args = (path,) if method == "download_location_input" else ()
    with pytest.raises(type(error)) as caught:
        client.resolve(getattr(client.client.results, method)(_ID, "places", *args))
    assert vars(caught.value) == vars(error)
    assert response.is_closed
    assert len(client.requests) == 1
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize("name", [12, None, b"places"])
def test_input_names_are_strict(
    client: ClientHarness, tmp_path: Path, method: str, name: object
) -> None:
    args = (tmp_path / "out",) if method == "download_location_input" else ()
    with pytest.raises((ValidationError, ValueError, TypeError)):
        client.resolve(getattr(client.client.results, method)(_ID, name, *args))
    assert not client.requests
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("method", _METHODS)
def test_reference_context_and_encoded_name(
    client: ClientHarness, tmp_path: Path, method: str
) -> None:
    name = "雪 /%?#"
    args = (tmp_path / "out",) if method == "download_location_input" else ()
    with pytest.raises(ValidationError):
        client.resolve(getattr(client.client.results, method)("bad", name, *args))
    assert not client.requests
    error = ResultNotFoundError(_ID, input_name=name)
    client.responses.append(
        httpx.Response(404, json={"detail": error_payload(error).model_dump()})
    )
    with pytest.raises(ResultNotFoundError):
        client.resolve(getattr(client.client.results, method)(_ID, name, *args))
    suffix = "rows" if method == "location_input_rows" else "download"
    assert str(
        client.requests[0].url
    ) == "https://example.test/api/" + location_input_download_path(
        _ID, name
    ).removesuffix("download") + suffix + ("?limit=100" if suffix == "rows" else "")
