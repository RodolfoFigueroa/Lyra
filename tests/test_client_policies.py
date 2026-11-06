from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiohttp
import geopandas as gpd
import pandas as pd
import pytest
from aiohttp.client_reqrep import ConnectionKey

from lyra.api import (
    AsyncJobHandle,
    AsyncLyraClient,
    DownloadError,
    JobHandle,
    JobNotReadyError,
    JobPollingError,
    JobWaitTimeoutError,
    MetricRunError,
    ServiceUnavailableError,
)
from lyra.contracts.jobs import TableJobResult
from tests.client_helpers import (
    ClientRuntime,
    FakeAsyncFile,
    FakeSession,
    _job_response,
    _readiness_response,
    _redis_unavailable_response,
    _result_response,
)
from tests.client_scenarios import AsyncReply, Reply, Scenario
from tests.provenance_helpers import result_provenance
from tests.result_helpers import inspection_of

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.jobs import JobProgress
    from lyra.contracts.types import JsonValue


@pytest.mark.parametrize(
    ("status", "text"),
    [(200, "not json"), (200, "{}"), (503, "oops"), (503, "{}"), (401, "denied")],
)
@pytest.mark.parametrize("resource", ["health", "submission", "admin", "result"])
def test_request_failures_have_context_and_close(
    scenario: Scenario, status: int, text: str, resource: str
) -> None:
    reply = Reply(status=status, text=text)
    scenario.replies = [reply]
    operations = {
        "health": scenario.client.health.liveness,
        "submission": lambda: scenario.client.raw.create("metric", {}),
        "admin": scenario.admin.status,
        "result": lambda: scenario.client.results.get("job-1"),
    }
    with pytest.raises(DownloadError, match="Failed to") as error:
        scenario.resolve(operations[resource]())
    if status == 200 and resource != "submission":
        assert error.value.__cause__ is not None
    assert reply.closed
    assert len(scenario.calls) == 1


@pytest.mark.parametrize(
    ("retry_after", "expected"), [("4", 4), ("invalid", None), (None, None)]
)
@pytest.mark.parametrize("resource", ["health", "submission", "admin", "download"])
def test_structured_unavailability(
    scenario: Scenario,
    tmp_path: Path,
    retry_after: str | None,
    expected: int | None,
    resource: str,
) -> None:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    scenario.replies = [
        Reply(status=503, payload=_redis_unavailable_response(), headers=headers)
    ]
    operations = {
        "health": scenario.client.health.liveness,
        "submission": lambda: scenario.client.raw.create("metric", {}),
        "admin": scenario.admin.status,
        "download": lambda: scenario.client.results.download(
            "job-1", tmp_path / "result"
        ),
    }
    with pytest.raises(ServiceUnavailableError) as error:
        scenario.resolve(operations[resource]())
    assert error.value.retry_after_seconds == expected
    assert error.value.code == "redis_unavailable"
    assert error.value.retryable
    assert len(scenario.calls) == 1


def test_readiness_503_and_submission_contract(scenario: Scenario) -> None:
    payload = _readiness_response() | {"status": "not_ready"}
    scenario.replies = [
        Reply(status=503, payload=payload),
        Reply(status=202, payload=_job_response()),
    ]
    assert scenario.resolve(scenario.client.health.readiness()).status == "not_ready"
    scenario.resolve(scenario.client.raw.create("metric", {"value": 3}))
    assert scenario.calls[0][2]["headers"] == {}
    assert scenario.calls[1][:2] == ("POST", "https://example.test/jobs")
    assert scenario.calls[1][2]["headers"] == {"Authorization": "Bearer consumer"}
    assert scenario.calls[1][2]["json"] == {
        "metric": "metric",
        "input": {"value": 3},
    }


def test_transport_timeout_is_chained_without_retry(scenario: Scenario) -> None:
    cause = TimeoutError("late")
    scenario.replies = [cause]
    with pytest.raises(DownloadError, match="create job") as error:
        scenario.resolve(scenario.client.raw.create("metric", {}))
    assert error.value.__cause__ is cause
    assert len(scenario.calls) == 1


@pytest.mark.parametrize("status", ["failed"])
def test_failed_handle_and_raw_results(scenario: Scenario, status: str) -> None:
    payload: dict[str, Any] = {
        "job_id": "job-1",
        "status": status,
        "kind": status,
        "error": {"message": "stopped"},
    }
    scenario.replies = [
        Reply(status=202, payload=_job_response()),
        Reply(payload=payload),
        Reply(payload=payload),
    ]
    handle = scenario.resolve(scenario.client.raw.submit("metric", {}))
    with pytest.raises(MetricRunError) as error:
        scenario.resolve(handle.result())
    assert error.value.status == status
    assert scenario.resolve(scenario.client.results.get("job-1")).status == status


@pytest.mark.parametrize("status", [404, 409, 500])
def test_download_error_preserves_destination(
    scenario: Scenario, tmp_path: Path, status: int
) -> None:
    reply = Reply(status=status, text="unavailable")
    scenario.replies = [reply]
    path = tmp_path / "result"
    path.write_bytes(b"original")
    with pytest.raises(DownloadError):
        scenario.resolve(scenario.client.results.download("job-1", path))
    assert path.read_bytes() == b"original"
    assert reply.closed


def test_native_dataframe_projection_is_absent(scenario: Scenario) -> None:
    assert not hasattr(scenario.client.results, "dataframe")
    assert not hasattr(scenario.client.results, "download_file")


@pytest.mark.parametrize("reference", ["job-1", "lyra://results/job-1"])
def test_streaming_download_and_cleanup(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reference: str
) -> None:
    reply = Reply(
        headers={"content-type": "application/octet-stream"}, chunks=[b"one", b"two"]
    )
    scenario.replies = [reply]
    monkeypatch.setattr(
        "lyra.api.client.http.aiofiles.open", lambda path, _mode: FakeAsyncFile(path)
    )
    path = tmp_path / "result"
    scenario.resolve(scenario.client.results.download(reference, path))
    assert path.read_bytes() == b"onetwo"
    assert reply.closed
    assert scenario.calls[0][2]["headers"] == {"Authorization": "Bearer consumer"}


def test_file_artifact_download_is_absent(scenario: Scenario) -> None:
    assert not hasattr(scenario.client.results, "dataframe")
    assert not hasattr(scenario.client.results, "download_file")


def test_non_json_raw_result_is_rejected(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(headers={"content-type": "application/octet-stream"}, text="bytes")
    ]
    with pytest.raises(DownloadError, match="response was not JSON"):
        scenario.resolve(scenario.client.results.get("job-1"))


@pytest.mark.parametrize("operation", ["request", "download"])
def test_async_invalid_response_encoding_is_chained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    cause = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    reply = Reply(status=400)

    class InvalidEncodingResponse(AsyncReply):
        async def text(self) -> str:
            await super().text()
            raise cause

    FakeSession.responses = [InvalidEncodingResponse(reply)]
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    client = AsyncLyraClient("example.test")
    call = (
        client.health.liveness()
        if operation == "request"
        else client.results.download("job-1", tmp_path / "result")
    )
    with pytest.raises(DownloadError) as error:
        asyncio.run(call)
    assert error.value.__cause__ is cause
    assert reply.closed


def status_reply(
    status: str = "running", *, current: float | None = None, stamp: int = 0
) -> Reply:
    payload: dict[str, Any] = {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": status,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    if current is not None:
        payload["progress"] = {
            "timestamp": f"2026-01-01T00:00:0{stamp}Z",
            "stage": "work",
            "current": current,
        }
    return Reply(payload=payload)


def submit_handle(scenario: Scenario) -> JobHandle | AsyncJobHandle:
    scenario.replies.insert(0, Reply(status=202, payload=_job_response()))
    return scenario.resolve(scenario.client.raw.submit("heavy_metric", {}))


def test_wait_polls_and_compares_progress_content(scenario: Scenario) -> None:
    scenario.replies = [
        status_reply(current=2),
        status_reply(current=2, stamp=1),
        status_reply(current=1, stamp=2),
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    seen: list[float] = []
    handle = submit_handle(scenario)

    def progress(snapshot: JobProgress) -> None:
        seen.append(snapshot.current)

    assert scenario.resolve(handle.wait(on_progress=progress)).status == "succeeded"
    assert seen == [2, 1]
    assert scenario.sleeps == [5.5, 5.5, 5.5]
    assert len(scenario.calls) == 5
    assert all("events" not in url for _, url, _ in scenario.calls)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_wait_retries_transient_status_with_guidance(
    scenario: Scenario, status: int
) -> None:
    scenario.replies = [
        Reply(status=status, text="temporary", headers={"Retry-After": "3"}),
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    assert scenario.resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert scenario.sleeps == [3]


def test_wait_retries_status_and_resets_counter(scenario: Scenario) -> None:
    scenario.replies = [
        *[Reply(status=503)] * 5,
        status_reply(),
        *[Reply(status=503)] * 5,
        status_reply("succeeded"),
    ]
    assert scenario.resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert scenario.sleeps[:5] == scenario.sleeps[6:]


def test_wait_exhausts_five_retries(scenario: Scenario) -> None:
    scenario.replies = [Reply(status=503)] * 6
    with pytest.raises(JobPollingError) as exc:
        scenario.resolve(submit_handle(scenario).wait())
    assert exc.value.job_id == "job-1"
    assert exc.value.attempts == 5
    assert len(scenario.sleeps) == 5


@pytest.mark.parametrize("seconds", [0, 2])
def test_wait_deadline_bounds_observations_and_sleep(
    scenario: Scenario, seconds: int
) -> None:
    scenario.replies = [status_reply()]
    with pytest.raises(JobWaitTimeoutError) as exc:
        scenario.resolve(submit_handle(scenario).wait(timeout=seconds))
    assert exc.value.job_id == "job-1"
    assert scenario.now == seconds
    assert len(scenario.calls) == (1 if seconds == 0 else 2)
    if seconds:
        assert scenario.timeouts[-1].total == seconds
        assert scenario.sleeps == [seconds]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_wait_permanent_errors_do_not_retry(scenario: Scenario, status: int) -> None:
    scenario.replies = [Reply(status=status)]
    with pytest.raises(DownloadError):
        scenario.resolve(submit_handle(scenario).wait())
    assert scenario.sleeps == []


def test_wait_explicit_nonretryable_service_response(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(
            status=503,
            payload={
                "detail": {"code": "unavailable", "message": "stop", "retryable": False}
            },
        )
    ]
    with pytest.raises(ServiceUnavailableError):
        scenario.resolve(submit_handle(scenario).wait())
    assert scenario.sleeps == []


@pytest.mark.parametrize("status", ["failed"])
def test_wait_raises_terminal_failure(scenario: Scenario, status: str) -> None:
    scenario.replies = [
        status_reply(status),
        Reply(
            payload={
                "job_id": "job-1",
                "kind": status,
                "status": status,
                "error": {"type": "test"},
            }
        ),
    ]
    with pytest.raises(MetricRunError) as exc:
        scenario.resolve(submit_handle(scenario).wait())
    assert exc.value.status == status


def test_progress_callback_failure_is_not_retried(scenario: Scenario) -> None:
    error = DownloadError("callback failed", retryable=True)

    def callback(_: JobProgress) -> None:
        raise error

    scenario.replies = [status_reply(current=1)]
    with pytest.raises(DownloadError) as exc:
        scenario.resolve(submit_handle(scenario).wait(on_progress=callback))
    assert exc.value is error
    assert scenario.sleeps == []


def test_async_progress_callback_is_awaited(scenario: Scenario) -> None:
    if not scenario.asynchronous:
        return
    seen: list[float] = []

    async def callback(snapshot: JobProgress) -> None:
        await asyncio.sleep(0)
        seen.append(snapshot.current)

    scenario.replies = [
        status_reply("succeeded", current=1),
        Reply(payload=_result_response()),
    ]
    handle = submit_handle(scenario)
    assert isinstance(handle, AsyncJobHandle)
    scenario.resolve(handle.wait(on_progress=callback))
    assert seen == [1]


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_poll_interval_must_be_positive_finite(
    scenario: Scenario, interval: float
) -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        scenario.resolve(submit_handle(scenario).wait(poll_interval=interval))


def test_wait_transport_timeout_retry_and_certificate_failure(
    scenario: Scenario,
) -> None:
    transient = TimeoutError()
    scenario.replies = [
        transient,
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    assert scenario.resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert len(scenario.sleeps) == 1
    permanent = aiohttp.ClientSSLError(
        ConnectionKey(
            host="test",
            port=443,
            is_ssl=True,
            ssl=True,
            proxy=None,
            proxy_auth=None,
            proxy_headers_hash=None,
        ),
        OSError("certificate"),
    )
    scenario.replies = [permanent]
    with pytest.raises(DownloadError):
        scenario.resolve(submit_handle(scenario).wait())
    assert len(scenario.sleeps) == 1


def test_async_cancelling_wait_does_not_cancel_remote_job(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    client_runtime: ClientRuntime,
) -> None:
    if not scenario.asynchronous:
        return
    scenario.replies = [status_reply()]
    handle = submit_handle(scenario)
    assert isinstance(handle, AsyncJobHandle)
    entered = asyncio.Event()

    async def sleep(_: float) -> None:
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(
        "lyra.api.client.async_.asyncio",
        SimpleNamespace(
            sleep=sleep,
            get_running_loop=asyncio.get_running_loop,
            shield=asyncio.shield,
        ),
    )

    async def run() -> None:
        task = asyncio.ensure_future(handle.wait())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    client_runtime.run(run())
    assert [method for method, _, _ in scenario.calls] == ["POST", "GET"]


def test_status_retries_are_bounded_by_deadline(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(status=503, headers={"Retry-After": "60"}),
    ]
    with pytest.raises(JobWaitTimeoutError):
        scenario.resolve(submit_handle(scenario).wait(timeout=2))
    assert scenario.now == 2
    assert scenario.sleeps == [2]


def test_malformed_polling_payload_is_permanent(scenario: Scenario) -> None:
    scenario.replies = [Reply(text="not json")]
    with pytest.raises(DownloadError):
        scenario.resolve(submit_handle(scenario).wait())
    assert not scenario.sleeps


@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=502, text="gateway failed"),
        Reply(status=202, text="broken response"),
    ],
)
def test_ambiguous_submission_explains_duplicate_risk_without_retry(
    scenario: Scenario, reply: Reply
) -> None:
    scenario.replies = [reply]
    with pytest.raises(DownloadError, match=r"may have been accepted.*duplicate"):
        scenario.resolve(scenario.client.raw.create("metric", {}))
    assert len(scenario.calls) == 1


@pytest.mark.parametrize("reference", ["job-1", "lyra://results/job-1"])
@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize(
    "outputs",
    ["ordinary", "spatial", "mixed", "empty", "empty_ordinary", "empty_spatial"],
)
def test_named_native_tables(
    scenario: Scenario, reference: str, outputs: str, *, resource: bool
) -> None:

    spatial = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-99, 19]},
                "id": "internal-index",
                "properties": {"code": "b", "date": "2026-10-27", "value": None},
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {"code": "a", "date": None, "value": 2},
            },
        ],
    }
    ordinary = [
        {"code": "b", "date": "2026-10-27", "value": None},
        {"code": "a", "date": None, "value": 2},
    ]
    payload: dict[str, Any] = {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "outputs": {},
        "output_metadata": {},
    }
    columns = [
        {"name": name, "dtype": "str", "unit": None, "description": name}
        for name in ["code", "date", "value"]
    ]
    empty = outputs.startswith("empty")
    if outputs in {"ordinary", "mixed", "empty", "empty_ordinary"}:
        payload["outputs"]["summary"] = [] if empty else ordinary
        payload["output_metadata"]["summary"] = {"columns": columns}
    if outputs in {"spatial", "mixed", "empty", "empty_spatial"}:
        payload["outputs"]["parcels"] = (
            {"type": "FeatureCollection", "features": []} if empty else spatial
        )
        payload["output_metadata"]["parcels"] = {
            "columns": [
                *columns,
                {
                    "name": "shape",
                    "dtype": "geometry",
                    "description": "Geometry",
                    "unit": None,
                },
            ],
            "geometry": "shape",
        }
    scenario.replies = [Reply(payload=payload)]
    client = scenario.client
    tables = scenario.resolve(
        client.results.tables(reference)
        if resource
        else client.result_tables(reference)
    )
    assert set(tables) == set(payload["outputs"])
    for name, table in tables.items():
        assert "id" not in table.columns
        assert isinstance(table, pd.DataFrame)
        assert isinstance(table, gpd.GeoDataFrame) == (name == "parcels")
        assert table.index.tolist() == ([] if empty else [0, 1])
        assert table.code.tolist() == ([] if empty else ["b", "a"])
        if not empty:
            assert table.date.iloc[0] == "2026-10-27"
            assert pd.isna(table.value.iloc[0])
        if isinstance(table, gpd.GeoDataFrame):
            assert table.crs is not None
            assert table.crs.to_epsg() == 4326
            assert table.geometry.name == "shape"
            if not empty:
                assert table.geometry.iloc[0].x == -99
                assert table.geometry.iloc[1] is None
    assert len(scenario.calls) == 1
    assert scenario.calls[0][:2] == ("GET", "https://example.test/jobs/job-1/result")
    assert not hasattr(client, "result_dataframe")
    assert not hasattr(client.results, "dataframe")


@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize("status", [404, 409, 410, 503])
def test_tables_lifecycle_errors(
    scenario: Scenario, status: int, *, resource: bool
) -> None:
    scenario.replies = [Reply(status=status, text="result unavailable")]
    method = (
        scenario.client.results.tables if resource else scenario.client.result_tables
    )
    with pytest.raises(DownloadError, match=f"HTTP {status}"):
        scenario.resolve(method("job-1"))
    assert len(scenario.calls) == 1


def test_tables_failed_result(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(
            payload={
                "kind": "failed",
                "status": "failed",
                "job_id": "job-1",
                "error": {"message": "failed"},
            }
        )
    ]
    with pytest.raises(MetricRunError):
        scenario.resolve(scenario.client.result_tables("job-1"))


@pytest.mark.parametrize(
    "output",
    [
        None,
        "encoded",
        [1],
        {"type": "Point"},
        {"type": "FeatureCollection", "features": [None]},
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": [], "geometry": None}],
        },
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Bad"}}
            ],
        },
    ],
)
def test_tables_malformed_output(scenario: Scenario, output: JsonValue) -> None:
    payload = _result_response()
    payload["outputs"] = {"result": output}
    scenario.replies = [Reply(payload=payload)]
    with pytest.raises(DownloadError) as error:
        scenario.resolve(scenario.client.results.tables("job-1"))
    assert error.value.__cause__ is not None


def test_tables_structured_unavailability(scenario: Scenario) -> None:
    scenario.replies = [
        Reply(
            status=503,
            payload=_redis_unavailable_response(),
            headers={"Retry-After": "5"},
        )
    ]
    with pytest.raises(ServiceUnavailableError) as error:
        scenario.resolve(scenario.client.result_tables("job-1"))
    assert error.value.retry_after_seconds == 5


@pytest.mark.parametrize("operation", ["get", "tables", "download"])
def test_pending_result_operations(
    scenario: Scenario, tmp_path: Path, operation: str
) -> None:
    pending = status_reply(current=2)
    pending.status = 202
    scenario.replies = [pending]
    destination = tmp_path / "result.json"
    destination.write_text("existing")
    call = getattr(scenario.client.results, operation)
    with pytest.raises(JobNotReadyError) as caught:
        scenario.resolve(
            call("job-1", destination) if operation == "download" else call("job-1")
        )
    assert caught.value.job_id == "job-1"
    assert caught.value.snapshot.status == "running"
    assert caught.value.snapshot.progress is not None
    assert caught.value.snapshot.progress.current == 2
    assert destination.read_text() == "existing"


@pytest.mark.parametrize("payload", [{}, {"status": "running"}, None])
def test_malformed_pending_response(scenario: Scenario, payload: JsonValue) -> None:
    scenario.replies = [Reply(status=202, payload=payload)]
    with pytest.raises(DownloadError, match="invalid JSON response"):
        scenario.resolve(scenario.client.results.get("job-1"))


def test_terminal_pending_response_is_invalid(scenario: Scenario) -> None:
    pending = status_reply("succeeded")
    pending.status = 202
    scenario.replies = [pending]
    with pytest.raises(DownloadError, match="invalid JSON response"):
        scenario.resolve(scenario.client.results.get("job-1"))


def test_wait_running_status_resets_retries_and_reports_progress(
    scenario: Scenario,
) -> None:
    pending = status_reply(current=3)
    scenario.replies = [
        *[Reply(status=503)] * 5,
        pending,
        *[Reply(status=503)] * 5,
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    seen = []
    assert (
        scenario.resolve(
            submit_handle(scenario).wait(on_progress=lambda p: seen.append(p.current))
        ).status
        == "succeeded"
    )
    assert seen == [3]
    assert 5.5 in scenario.sleeps


def test_pending_result_respects_deadline(scenario: Scenario) -> None:
    pending = status_reply()
    scenario.replies = [pending]
    with pytest.raises(JobWaitTimeoutError):
        scenario.resolve(submit_handle(scenario).wait(timeout=2))


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "Metric is not public"), (422, "Edition is required")],
)
def test_capability_submission_rejection(
    scenario: Scenario, status: int, message: str
) -> None:
    scenario.replies = [Reply(status=status, payload={"detail": message})]
    with pytest.raises(DownloadError, match=message) as error:
        scenario.resolve(scenario.client.raw.submit("metric", {}))
    assert not error.value.retryable
    assert len(scenario.calls) == 1
    assert scenario.calls[0][2]["json"] == {"metric": "metric", "input": {}}


@pytest.mark.parametrize("preview", [False, True])
def test_capability_result_context(scenario: Scenario, *, preview: bool) -> None:
    payload = _result_response()
    provenance = {
        "metric": "metric",
        "edition": "2025",
        "cache_revision": "fresh",
        "catalog_fingerprint": "catalog-1",
        "plugin": {"name": "test-plugin", "version": "1"},
        "input": {"parameters": {"count": 3}},
        "outputs": payload["output_metadata"],
        "created_at": "2026-07-09T12:00:00Z",
    }
    provenance = result_provenance(provenance).model_dump(mode="json")
    payload["provenance"] = provenance
    scenario.replies = [
        Reply(
            payload=inspection_of(TableJobResult.model_validate(payload)).model_dump(
                mode="json"
            )
            if preview
            else payload
        )
    ]
    fetch = scenario.client.results.inspect if preview else scenario.client.results.get
    result = scenario.resolve(fetch("job-1"))
    assert result.provenance is not None
    if preview:
        assert result.provenance.edition == "2025"
        assert result.provenance.cache_revision == "fresh"
        assert result.outputs["result"].metadata.declaration.columns == []
    else:
        assert (
            result.provenance.model_dump(mode="json", exclude_unset=True) == provenance
        )
        assert result.output_metadata["result"].columns == []
    assert scenario.calls[0][2]["headers"] == {"Authorization": "Bearer consumer"}
