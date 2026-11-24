"""Remote export errors preserve destinations and client usability."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from lyra.api import AsyncLyraClient
from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from test_support.publication import publish_result
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database
from tests.table_client_helpers import ClientHarness, client

__all__ = ["client", "database"]


@pytest.mark.parametrize(
    "method", ["export_geoparquet", "export_location_input_geoparquet"]
)
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "error_type",
    [
        ResultNotFoundError,
        ResultIntegrityError,
        LocationInputUnavailableError,
    ],
)
def test_remote_lookup_errors(
    client: ClientHarness,
    tmp_path: Path,
    method: str,
    error_type: type[ResultAccessError],
    *,
    existing: bool,
) -> None:
    reference = "a" * 32
    if error_type is LocationInputUnavailableError:
        if method == "export_geoparquet":
            return
        error = LocationInputUnavailableError(reference, "selected")
    else:
        error = error_type(
            reference,
            **(
                {"input_name": "selected"}
                if method == "export_location_input_geoparquet"
                else {"output_name": "selected"}
            ),
        )
    payload = error_payload(error)
    client.responses.append(
        httpx.Response(error_status(payload), json={"detail": payload.model_dump()})
    )
    path = tmp_path / "export.parquet"
    if existing:
        path.write_bytes(b"original")
    with pytest.raises(error_type) as caught:
        client.resolve(
            getattr(client.client.results, method)(reference, "selected", path)
        )
    assert vars(caught.value) == vars(error)
    assert list(tmp_path.iterdir()) == ([path] if existing else [])
    if existing:
        assert path.read_bytes() == b"original"
    client.responses.append(
        httpx.Response(
            200,
            json={
                "job_id": reference,
                "metric_name": "metric",
                "parameters": {},
                "outputs": {},
            },
        )
    )
    assert client.resolve(client.client.results.describe(reference)).job_id == reference


@pytest.mark.parametrize(
    "failure",
    [
        "reference",
        "name",
        "missing",
        "damage",
        "nonspatial",
        "empty-nonspatial",
        "parent",
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_lookup_and_destination_errors(
    database: Database,
    tmp_path: Path,
    failure: str,
    client: ClientHarness,
    *,
    existing: bool,
) -> None:
    writer = SQLiteStore(tmp_path / "store.sqlite")
    retained = publish_values(writer, ("one",), spatial="nonspatial" not in failure)
    reference = publish_result(ResultDescriptions(writer), retained)
    name = "b" if failure == "empty-nonspatial" else "a"
    expected = ValueError
    if failure == "reference":
        reference, expected = "bad", ValidationError
    elif failure == "name":
        name, expected = "missing", ResultNotFoundError
    elif failure == "missing":
        reference, expected = "0" * 32, ResultNotFoundError
    elif failure == "damage":
        database.rows["stored_results"].clear()
        expected = ResultIntegrityError
    destination = tmp_path / "export.parquet"
    if existing:
        destination.write_bytes(b"original")
    if failure == "parent":
        destination = tmp_path / "missing" / "export.parquet"
        expected = FileNotFoundError
    before = set(tmp_path.iterdir())
    with pytest.MonkeyPatch.context() as monkeypatch:
        reader = ResultStore(writer)
        factory = AsyncMock if isinstance(client.client, AsyncLyraClient) else Mock
        monkeypatch.setattr(
            client.client.results, "table", factory(side_effect=reader.read_table)
        )
        monkeypatch.setattr(
            client.client.results, "describe", factory(side_effect=reader.describe)
        )
        with pytest.raises(expected):
            client.resolve(
                client.client.results.export_geoparquet(reference, name, destination)
            )
    assert set(tmp_path.iterdir()) == before
    if existing:
        assert (tmp_path / "export.parquet").read_bytes() == b"original"
    else:
        assert not destination.exists()


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("input_export", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_missing_selected_description(
    client: ClientHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    input_export: bool,
    existing: bool,
) -> None:
    store = SQLiteStore(tmp_path / "store")
    reference = publish_result(
        ResultDescriptions(store), publish_values(store, ("one",), spatial=True)
    )
    reader = ResultStore(store)
    frame = reader.read_table(reference, "a")
    description = reader.describe(reference)
    description.outputs.clear()
    description.location_inputs.clear()
    factory = AsyncMock if isinstance(client.client, AsyncLyraClient) else Mock
    monkeypatch.setattr(
        client.client.results,
        "location_input" if input_export else "table",
        factory(return_value=frame),
    )
    monkeypatch.setattr(
        client.client.results, "describe", factory(return_value=description)
    )
    path = tmp_path / "export.parquet"
    if existing:
        path.write_bytes(b"original")
    export = (
        client.client.results.export_location_input_geoparquet
        if input_export
        else client.client.results.export_geoparquet
    )
    with pytest.raises(ResultIntegrityError) as caught:
        client.resolve(export(reference, "a", path))
    assert caught.value.job_id == reference
    assert (
        caught.value.input_name if input_export else caught.value.output_name
    ) == "a"
    assert list(tmp_path.iterdir()) == ([path] if existing else [])
    if existing:
        assert path.read_bytes() == b"original"
