from __future__ import annotations

import asyncio
import ssl
import threading
from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import httpx
import pandas as pd
import pytest

from lyra.api import (
    AsyncJobHandle,
    AsyncLyraClient,
    DownloadError,
    JobHandle,
    JobNotReadyError,
    JobPollingError,
    JobWaitTimeoutError,
    MetricRunError,
    RunOptions,
    ServiceUnavailableError,
)
from lyra.contracts.jobs import JobCreateResponse, JobStatusInfo, TableJobResult
from tests.client_helpers import (
    ClientRuntime,
    FakeSession,
    _job_response,
    _readiness_response,
    _redis_unavailable_response,
    _result_response,
    _status_response,
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
        "submission": lambda: scenario.client.jobs.create(
            "metric", inputs={}, parameters={}
        ),
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
        "submission": lambda: scenario.client.jobs.create(
            "metric", inputs={}, parameters={}
        ),
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
    scenario.resolve(
        scenario.client.jobs.create("metric", inputs={}, parameters={"value": 3})
    )
    assert scenario.calls[0][2]["headers"] == {}
    assert scenario.calls[1][:2] == ("POST", "https://example.test/jobs")
    assert scenario.calls[1][2]["headers"] == {"Authorization": "Bearer consumer"}
    assert scenario.calls[1][2]["json"] == {
        "metric": "metric",
        "inputs": {},
        "parameters": {"value": 3},
    }


def test_transport_timeout_is_chained_without_retry(scenario: Scenario) -> None:
    cause = httpx.ReadTimeout("late")
    scenario.replies = [cause]
    with pytest.raises(DownloadError, match="create job") as error:
        scenario.resolve(
            scenario.client.jobs.create("metric", inputs={}, parameters={})
        )
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
    handle = scenario.resolve(
        scenario.client.jobs.submit("metric", inputs={}, parameters={})
    )
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


@pytest.mark.parametrize("reference", ["job-1", "lyra://results/job-1"])
def test_streaming_download_and_cleanup(
    scenario: Scenario, tmp_path: Path, reference: str
) -> None:
    reply = Reply(
        headers={"content-type": "application/octet-stream"}, chunks=[b"one", b"two"]
    )
    scenario.replies = [reply]
    path = tmp_path / "result"
    scenario.resolve(scenario.client.results.download(reference, path))
    assert path.read_bytes() == b"onetwo"
    assert reply.closed
    assert scenario.calls[0][2]["headers"] == {"Authorization": "Bearer consumer"}


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
    cause = httpx.DecodingError("invalid encoding")
    reply = Reply(status=400)

    class InvalidEncodingResponse(AsyncReply):
        async def text(self) -> str:
            await super().text()
            raise cause

    FakeSession.responses = [InvalidEncodingResponse(reply)]
    monkeypatch.setattr(httpx, "AsyncClient", FakeSession)
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
    return scenario.resolve(
        scenario.client.jobs.submit("heavy_metric", inputs={}, parameters={})
    )


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
    assert isinstance(exc.value.__cause__, DownloadError)
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
        assert scenario.timeouts[-1].read == 30
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
    assert exc.value.job_id == "job-1"


def test_progress_callback_failure_is_not_retried(scenario: Scenario) -> None:
    error = DownloadError("callback failed", retryable=True)
    caller_thread = threading.get_ident()

    def callback(_: JobProgress) -> None:
        if not scenario.asynchronous:
            assert threading.get_ident() == caller_thread
        raise error

    scenario.replies = [status_reply(current=1)]
    with pytest.raises(DownloadError) as exc:
        scenario.resolve(submit_handle(scenario).wait(on_progress=callback))
    assert exc.value is error
    assert scenario.sleeps == []


@pytest.mark.parametrize("fails", [False, True])
def test_async_progress_callback_is_awaited(scenario: Scenario, *, fails: bool) -> None:
    if not scenario.asynchronous:
        return
    seen: list[float] = []
    error = DownloadError("async callback failed", retryable=True)

    async def callback(snapshot: JobProgress) -> None:
        await asyncio.sleep(0)
        seen.append(snapshot.current)
        if fails:
            raise error

    scenario.replies = [
        status_reply("succeeded", current=1),
        Reply(payload=_result_response()),
    ]
    handle = submit_handle(scenario)
    assert isinstance(handle, AsyncJobHandle)
    if fails:
        with pytest.raises(DownloadError) as exc:
            scenario.resolve(handle.wait(on_progress=callback))
        assert exc.value is error
        assert scenario.sleeps == []
    else:
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
    transient = httpx.ReadTimeout("late")
    scenario.replies = [
        transient,
        status_reply("succeeded"),
        Reply(payload=_result_response()),
    ]
    assert scenario.resolve(submit_handle(scenario).wait()).status == "succeeded"
    assert len(scenario.sleeps) == 1
    permanent = httpx.ConnectError("certificate")
    permanent.__cause__ = ssl.SSLError("certificate")
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
        scenario.resolve(
            scenario.client.jobs.create("metric", inputs={}, parameters={})
        )
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
        scenario.resolve(
            scenario.client.jobs.submit("metric", inputs={}, parameters={})
        )
    assert not error.value.retryable
    assert len(scenario.calls) == 1
    assert scenario.calls[0][2]["json"] == {
        "metric": "metric",
        "inputs": {},
        "parameters": {},
    }


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


@pytest.mark.parametrize("method", ["create", "submit", "run", "run_to_file"])
@pytest.mark.parametrize(
    "sections",
    [
        {},
        {"inputs": None, "parameters": None},
        {"inputs": {}, "parameters": {}},
        {
            "inputs": {"area": {"ids": ["one"]}},
            "parameters": {"value": 3, "nested": [None, {"enabled": True}]},
        },
    ],
)
def test_jobs_helpers_submit_independent_jobs(
    scenario: Scenario, tmp_path: Path, method: str, sections: dict[str, Any]
) -> None:
    original = deepcopy(sections)
    client = scenario.client
    for number in (1, 2):
        job_id = f"job-{number}"
        scenario.replies = [
            Reply(status=202, payload=_job_response() | {"job_id": job_id})
        ]
        if method in {"run", "run_to_file"}:
            scenario.replies.append(
                Reply(
                    payload=_status_response()
                    | {"job_id": job_id, "status": "succeeded"}
                )
            )
            scenario.replies.append(
                Reply(payload=_result_response() | {"job_id": job_id})
                if method == "run"
                else Reply(chunks=[b'{"outputs":', b"{}}"])
            )
        options = {"edition": "2025", "cache_revision": "dataset-1"}
        if method == "create":
            result = scenario.resolve(
                client.jobs.create("heavy_metric", **sections, **options)
            )
            assert isinstance(result, JobCreateResponse)
            assert result.job_id == job_id
        elif method == "submit":
            handle = scenario.resolve(
                client.jobs.submit("heavy_metric", **sections, **options)
            )
            assert isinstance(
                handle, AsyncJobHandle if scenario.asynchronous else JobHandle
            )
            assert handle.job_id == job_id
        elif method == "run":
            result = scenario.resolve(
                client.jobs.run(
                    "heavy_metric",
                    **sections,
                    options=RunOptions(timeout=10),
                    **options,
                )
            )
            assert isinstance(result, TableJobResult)
            assert result.job_id == job_id
            assert result.outputs == {"result": [{"value": 6}]}
        else:
            path = tmp_path / f"{job_id}.json"
            assert (
                scenario.resolve(
                    client.jobs.run_to_file(
                        "heavy_metric",
                        **sections,
                        path=path,
                        options=RunOptions(timeout=10),
                        **options,
                    )
                )
                is None
            )
            assert path.read_bytes() == b'{"outputs":{}}'
        assert not scenario.replies

    submissions = [call for call in scenario.calls if call[0] == "POST"]
    assert len(submissions) == 2
    for _, url, request in submissions:
        assert url == "https://example.test/jobs"
        assert request["json"] == {
            "metric": "heavy_metric",
            "inputs": sections.get("inputs") or {},
            "parameters": sections.get("parameters") or {},
            "edition": "2025",
            "cache_revision": "dataset-1",
        }

    assert sections == original


def test_jobs_observation_only_reads_status(scenario: Scenario) -> None:
    client = scenario.client
    scenario.replies = [Reply(status=202, payload=_job_response())]
    handle = scenario.resolve(
        client.jobs.submit("heavy_metric", inputs={}, parameters={})
    )
    scenario.calls.clear()
    scenario.replies = [
        Reply(payload=_status_response()),
        Reply(payload=_status_response() | {"status": "succeeded"}),
    ]
    current = scenario.resolve(client.jobs.get(handle.job_id))
    completed = scenario.resolve(handle.wait())
    assert isinstance(current, JobStatusInfo)
    assert current.status == "running"
    assert isinstance(completed, JobStatusInfo)
    assert completed.status == "succeeded"
    assert [call[:2] for call in scenario.calls] == [
        ("GET", "https://example.test/jobs/job-1"),
        ("GET", "https://example.test/jobs/job-1"),
    ]


@pytest.mark.parametrize(
    ("operation", "expiry"),
    [
        (operation, expiry)
        for operation in ("wait", "run", "run_to_file")
        for expiry in ("observation", "retrieval", "none")
        if (operation, expiry) != ("wait", "retrieval")
    ],
)
def test_deadlines_check_after_operations_settle(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
    expiry: str,
) -> None:
    submission = Reply(status=202, payload=_job_response())
    observation = status_reply("succeeded")
    retrieval = (
        Reply(chunks=[b"result"])
        if operation == "run_to_file"
        else Reply(payload=_result_response())
    )
    scenario.replies = [submission, observation, retrieval]
    original = Reply.close

    def settle(self: Reply) -> None:
        original(self)
        if self is submission:
            scenario.advance(100)  # Submission is outside the run budget.
        elif (self is observation and expiry == "observation") or (
            self is retrieval and expiry == "retrieval"
        ):
            scenario.advance(11)

    monkeypatch.setattr(Reply, "close", settle)
    client = scenario.client
    target = tmp_path / "result.json"

    def execute() -> object:
        if operation == "wait":
            handle = scenario.resolve(client.jobs.submit("metric"))
            return scenario.resolve(handle.wait(timeout=10))
        if operation == "run":
            return scenario.resolve(
                client.jobs.run("metric", options=RunOptions(timeout=10))
            )
        return scenario.resolve(
            client.jobs.run_to_file(
                "metric", path=target, options=RunOptions(timeout=10)
            )
        )

    if expiry == "none":
        execute()
    else:
        with pytest.raises(JobWaitTimeoutError) as error:
            execute()
        assert error.value.job_id == "job-1"
    assert submission.closed
    assert observation.closed
    fetched = operation != "wait" and expiry != "observation"
    assert retrieval.closed == fetched
    assert len(scenario.calls) == (3 if fetched else 2)
    assert all(timeout.read == 30 for timeout in scenario.timeouts)
    assert scenario.sleeps == []
    if operation == "run_to_file" and fetched:
        assert target.read_bytes() == b"result"
    # The client remains usable for later observation/retrieval after a timeout.
    scenario.replies = [Reply(payload=_result_response())]
    assert scenario.resolve(client.results.get("job-1")).job_id == "job-1"
    assert scenario.timeouts[-1].read == 30


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_wait_rejects_invalid_deadlines(scenario: Scenario, timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        scenario.resolve(submit_handle(scenario).wait(timeout=timeout))
    assert len(scenario.calls) == 1


def test_callback_expiry_prevents_further_observation(scenario: Scenario) -> None:
    scenario.replies = [status_reply(current=1)]
    handle = submit_handle(scenario)

    def progress(_: JobProgress) -> None:
        scenario.advance(11)

    with pytest.raises(JobWaitTimeoutError):
        scenario.resolve(handle.wait(timeout=10, on_progress=progress))
    assert len(scenario.calls) == 2
    assert scenario.sleeps == []


@pytest.mark.parametrize("download", [False, True])
@pytest.mark.parametrize("expires", [False, True])
def test_run_retrieval_failure_checks_deadline(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    download: bool,
    expires: bool,
) -> None:
    failure = Reply(status=503, payload=_redis_unavailable_response())
    scenario.replies = [
        Reply(status=202, payload=_job_response()),
        status_reply("succeeded"),
        failure,
    ]
    original = Reply.close

    def settle(self: Reply) -> None:
        original(self)
        if self is failure and expires:
            scenario.advance(11)

    monkeypatch.setattr(Reply, "close", settle)
    client = scenario.client
    expected = JobWaitTimeoutError if expires else ServiceUnavailableError

    def execute() -> None:
        if download:
            scenario.resolve(
                client.jobs.run_to_file(
                    "metric", path=tmp_path / "result", options=RunOptions(timeout=10)
                )
            )
        else:
            scenario.resolve(client.jobs.run("metric", options=RunOptions(timeout=10)))

    with pytest.raises(expected) as error:
        execute()
    if expires:
        assert isinstance(error.value.__context__, ServiceUnavailableError)
    assert failure.closed
    assert len(scenario.calls) == 3
    assert scenario.sleeps == []
