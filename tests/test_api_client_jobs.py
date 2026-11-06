import asyncio
import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Unpack, cast

import aiohttp
import pytest
from lyra.api import parse_result_ref
from lyra.api.client.async_ import AsyncLyraAdminClient, AsyncLyraClient
from lyra.api.client.endpoints import RequestSpec, create_job, validate_response
from lyra.api.client.sync import LyraAdminClient, LyraClient
from lyra.api.exceptions import DownloadError, ServiceUnavailableError
from lyra.sdk.models.inspection import TerminalResultOutput
from lyra.sdk.models.job import (
    TableJobResult,
)

from tests.client_helpers import (
    ClientRuntime,
    FakeAsyncFile,
    FakeAsyncResponse,
    FakeSession,
    FakeSyncResponse,
    SyncRequestOptions,
    _admin_status_response,
    _catalog_summary_response,
    _config_summary_response,
    _data_types_response,
    _job_detail_response,
    _job_list_response,
    _job_response,
    _liveness_response,
    _metric_catalog_response,
    _metric_response,
    _mock_sync_http,
    _plugin_repo_list_response,
    _plugin_routing_response,
    _queues_response,
    _readiness_response,
    _redis_unavailable_response,
    _result_descriptor_response,
    _result_response,
    _status_response,
    _worker_detail_response,
    _workers_response,
    install_sync_handler,
)
from tests.result_helpers import inspection_of

if TYPE_CHECKING:
    from lyra.sdk.types import JsonObject


def test_public_client_surfaces_and_credentials_are_separated() -> None:
    sync_consumer = LyraClient("example.test")
    async_consumer = AsyncLyraClient("example.test")
    sync_admin = LyraAdminClient("example.test")
    async_admin = AsyncLyraAdminClient("example.test")

    assert not hasattr(sync_consumer, "lookups")
    assert not hasattr(async_consumer, "lookups")
    assert not hasattr(sync_consumer, "admin")
    assert not hasattr(async_consumer, "admin")
    assert not hasattr(sync_admin, "raw")
    assert not hasattr(async_admin, "raw")
    assert hasattr(sync_admin, "health")
    assert hasattr(async_admin, "health")

    sync_consumer_parameters = inspect.signature(LyraClient).parameters
    async_consumer_parameters = inspect.signature(AsyncLyraClient).parameters
    sync_admin_parameters = inspect.signature(LyraAdminClient).parameters
    async_admin_parameters = inspect.signature(AsyncLyraAdminClient).parameters

    assert "agent_api_key" in sync_consumer_parameters
    assert "agent_api_key" in async_consumer_parameters
    assert "admin_api_key" not in sync_consumer_parameters
    assert "admin_api_key" not in async_consumer_parameters
    assert "admin_api_key" in sync_admin_parameters
    assert "admin_api_key" in async_admin_parameters
    assert "agent_api_key" not in sync_admin_parameters
    assert "agent_api_key" not in async_admin_parameters


def test_sync_client_uses_job_api_for_job_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, Any]] = []

    def post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        posted.append(
            {"url": url, "json": json, "timeout": timeout, "headers": headers}
        )
        return FakeSyncResponse(status_code=202, payload=_job_response())

    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool = False,  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        if url.endswith("/result"):
            return FakeSyncResponse(payload=_result_response())
        return FakeSyncResponse(payload={**_status_response(), "status": "succeeded"})

    _mock_sync_http(monkeypatch, post=post)
    _mock_sync_http(monkeypatch, get=get)
    client = LyraClient(
        "example.test",
        secure=False,
        timeout=12.0,
        agent_api_key="agent-secret",
    )

    job = client.raw.create("heavy_metric", {"value": 3})
    status = client.jobs.get(job.job_id)
    result = client.results.get(job.job_id)
    processed = client.raw.run("heavy_metric", {"value": 3})

    assert posted[0]["url"] == "http://example.test/jobs"
    assert posted[0]["json"] == {
        "metric": "heavy_metric",
        "input": {"value": 3},
    }
    assert posted[0]["headers"] == {"Authorization": "Bearer agent-secret"}
    assert job.job_id == "job-1"
    assert "reused" not in job.model_dump()
    assert status.status == "succeeded"
    assert result.kind == "table"
    assert result.outputs == {"result": [{"value": 6}]}
    assert isinstance(processed, TableJobResult)
    assert processed.outputs == {"result": [{"value": 6}]}


def test_sync_client_uses_admin_job_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests_seen: list[dict[str, Any]] = []

    def get(
        url: str,
        *,
        params: dict[str, int | str] | None = None,
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        requests_seen.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        return FakeSyncResponse(
            payload=_job_detail_response()
            if url.endswith("job-1")
            else _job_list_response()
        )

    def post(
        url: str,
        *,
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        requests_seen.append({"url": url, "timeout": timeout, "headers": headers})
        return FakeSyncResponse(payload=_job_detail_response())

    _mock_sync_http(monkeypatch, get=get)
    _mock_sync_http(monkeypatch, post=post)
    client = LyraAdminClient(
        "example.test",
        secure=False,
        timeout=12.0,
        admin_api_key="admin-secret",
    )

    jobs = client.jobs.list(limit=10, status="running", queue="heavy", offset=3)
    detail = client.jobs.get("job-1")

    assert requests_seen == [
        {
            "url": "http://example.test/admin/jobs",
            "params": {
                "limit": 10,
                "status": "running",
                "queue": "heavy",
                "offset": 3,
            },
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "url": "http://example.test/admin/jobs/job-1",
            "params": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
    ]
    assert [job.job_id for job in jobs.jobs] == ["job-1"]
    assert detail.snapshot.job_id == "job-1"
    assert detail.queue == "heavy"


def test_sync_client_uses_observability_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "http://example.test/live": _liveness_response(),
        "http://example.test/ready": _readiness_response(),
        "http://example.test/admin/status": _admin_status_response(),
        "http://example.test/admin/config-summary": _config_summary_response(),
        "http://example.test/admin/catalog": _catalog_summary_response(),
        "http://example.test/admin/workers": _workers_response(),
        "http://example.test/admin/workers/interactive": _worker_detail_response(),
        "http://example.test/admin/queues": _queues_response(),
    }
    seen: list[str] = []
    seen_headers: list[dict[str, str]] = []

    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        seen.append(url)
        seen_headers.append(headers)
        return FakeSyncResponse(payload=responses[url])

    _mock_sync_http(monkeypatch, get=get)
    client = LyraAdminClient(
        "example.test",
        secure=False,
        admin_api_key="admin-secret",
    )

    liveness = client.health.liveness()
    readiness = client.health.readiness()
    status = client.status()
    config = client.config_summary()
    catalog = client.catalog.summary()
    workers = client.workers.list()
    worker = client.workers.get("interactive")
    queues = client.queues.list()

    assert seen == list(responses)
    assert seen_headers[:2] == [{}, {}]
    assert seen_headers[2:] == [{"Authorization": "Bearer admin-secret"}] * 6
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert workers.workers[0].state == "busy"
    assert workers.workers[0].stale is False
    assert worker.current_job == "job-1"
    assert worker.heartbeat_age_seconds == pytest.approx(0.25)
    assert queues.queues[0].pending_depth_unknown is True


def test_sync_client_exposes_structured_database_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_sync_handler(
        monkeypatch,
        lambda *_args, **_kwargs: FakeSyncResponse(
            status_code=503,
            payload=_redis_unavailable_response(),
            headers={"Retry-After": "5"},
        ),
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        LyraClient("example.test", secure=False).raw.create("metric", {})

    assert exc_info.value.code == "redis_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5


def test_sync_client_uses_plugin_and_routing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _plugin_repo_list_response(),
        _plugin_routing_response(),
    ]
    requests_seen: list[dict[str, Any]] = []

    def request(
        method: str, url: str, **options: Unpack[SyncRequestOptions]
    ) -> FakeSyncResponse:
        requests_seen.append(
            {
                "method": method,
                "url": url,
                "params": options["params"],
                "json": options["json"],
                "timeout": options["timeout"],
                "headers": options["headers"],
            }
        )
        return FakeSyncResponse(payload=responses.pop(0))

    install_sync_handler(monkeypatch, request)
    admin = LyraAdminClient(
        "example.test/", secure=False, timeout=12.0, admin_api_key="admin-secret"
    )
    repos = admin.plugins.list()
    routing = admin.routing.list()
    assert requests_seen == [
        {
            "method": "GET",
            "url": "http://example.test/admin/plugins",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "GET",
            "url": "http://example.test/admin/plugin-routing",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
    ]
    assert repos.plugins[0].distribution == "lyra-smoke-plugin"
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}


def test_sync_client_reports_operator_route_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def request(
        _method: str,
        _url: str,
        **_options: Unpack[SyncRequestOptions],
    ) -> FakeSyncResponse:
        return FakeSyncResponse(status_code=409, text="plugin disabled")

    install_sync_handler(monkeypatch, request)

    with pytest.raises(
        DownloadError,
        match=r"Failed to list installed plugins\. HTTP 409: plugin disabled",
    ):
        LyraAdminClient("example.test", secure=False).plugins.list()


def test_sync_client_returns_location_selection_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/data-types"
        return FakeSyncResponse(payload=_data_types_response())

    _mock_sync_http(monkeypatch, get=get)
    response = LyraClient("example.test", secure=False).catalog.data_types()

    assert response.location_selection_schema == {"type": "object"}


def test_sync_client_returns_metric_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        params: dict[str, Any],
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert params == {"query": "", "offset": 0, "limit": 50}
        assert url == "http://example.test/metrics"
        return FakeSyncResponse(payload=_metric_catalog_response())

    _mock_sync_http(monkeypatch, get=get)
    catalog = LyraClient("example.test", secure=False).catalog.metrics()

    assert len(catalog) == 1
    assert catalog[0].name == "accessibility_by_destination"


def test_sync_client_returns_one_v5_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/metrics/accessibility_by_destination"
        return FakeSyncResponse(payload=_metric_response())

    _mock_sync_http(monkeypatch, get=get)
    metric = LyraClient("example.test", secure=False).catalog.metric(
        "accessibility_by_destination"
    )

    assert metric.family.name == "accessibility_by_destination"


def test_sync_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # ruff:ignore[unused-function-argument]
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        return FakeSyncResponse(payload={"location": []})

    _mock_sync_http(monkeypatch, get=get)

    with pytest.raises(
        DownloadError, match="Failed to fetch data types: invalid JSON response"
    ):
        LyraClient("example.test", secure=False).catalog.data_types()


def test_sync_client_downloads_file_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool,
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result/download"
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "application/json"},
            chunks=[b"abc", b"def"],
        )

    _mock_sync_http(monkeypatch, get=get)
    output = tmp_path / "result.json"

    LyraClient("example.test", secure=False).results.download(
        "job-1",
        output,
    )

    assert output.read_bytes() == b"abcdef"


def test_sync_client_fetches_named_result_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result"
        return FakeSyncResponse(payload=_result_response())

    _mock_sync_http(monkeypatch, get=get)

    result = LyraClient("example.test", secure=False).results.get("job-1")

    assert isinstance(result, TableJobResult)
    assert result.outputs == {"result": [{"value": 6}]}


def test_result_ref_parser_accepts_refs_and_raw_job_ids() -> None:
    assert parse_result_ref("lyra://results/job-1") == "job-1"
    assert parse_result_ref("job-1") == "job-1"

    with pytest.raises(DownloadError, match="Invalid Lyra result reference"):
        parse_result_ref("lyra://results/job-1/extra")

    with pytest.raises(DownloadError, match="Unsupported result reference"):
        parse_result_ref("https://example.test/results/job-1")


@pytest.mark.parametrize("unit", ["count", None])
def test_sync_client_fetches_result_descriptor_from_ref(
    monkeypatch: pytest.MonkeyPatch,
    unit: str | None,
) -> None:
    seen: list[str] = []

    def request(
        method: str,
        url: str,
        **options: Unpack[SyncRequestOptions],
    ) -> FakeSyncResponse:
        seen.append(f"{method} {url}")
        assert options["headers"] == {"Authorization": "Bearer agent-secret"}
        payload = _result_descriptor_response()
        payload["outputs"]["result"]["metadata"]["declaration"]["columns"][0][
            "unit"
        ] = unit
        return FakeSyncResponse(payload=payload)

    install_sync_handler(monkeypatch, request)

    descriptor = LyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).results.inspect("lyra://results/job-1")

    assert seen == ["GET http://example.test/jobs/job-1/result/inspection"]
    assert isinstance(descriptor, TerminalResultOutput)
    assert descriptor.outputs["result"].metadata is not None
    assert descriptor.completed_at is not None
    assert descriptor.result_ref == "lyra://results/job-1"
    assert "result" in descriptor.outputs
    assert descriptor.outputs["result"].metadata.declaration.columns[0].name == "value"
    assert descriptor.outputs["result"].metadata.declaration.columns[0].unit == unit
    assert descriptor.provenance is not None
    assert descriptor.provenance.plugin.version == "1.0.0"
    assert descriptor.outputs["result"].metadata.row_count == 1


def test_sync_client_downloads_json_result_from_raw_job_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],
        stream: bool,
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result/download"
        assert headers == {"Authorization": "Bearer agent-secret"}
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "application/json"},
            chunks=[json.dumps(_result_response()).encode()],
        )

    _mock_sync_http(monkeypatch, get=get)
    output = tmp_path / "result.json"

    LyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).results.download("job-1", output)

    assert json.loads(output.read_text()) == _result_response()


def test_sync_client_reports_result_download_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # ruff:ignore[unused-function-argument]
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool,  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        return FakeSyncResponse(status_code=409, text="result is not a table")

    _mock_sync_http(monkeypatch, get=get)

    with pytest.raises(
        DownloadError,
        match=r"Failed to download result\. HTTP 409: result is not a table",
    ):
        LyraClient("example.test", secure=False).results.download(
            "job-1",
            tmp_path / "result.json",
        )


def test_native_dataframe_projection_is_absent() -> None:
    assert not hasattr(LyraClient("unused.test").results, "dataframe")
    assert not hasattr(AsyncLyraClient("unused.test").results, "dataframe")


def test_file_artifact_download_is_absent() -> None:
    assert not hasattr(LyraClient("unused.test").results, "download_file")
    assert not hasattr(AsyncLyraClient("unused.test").results, "download_file")


def test_async_client_processes_json_job(
    client_runtime: ClientRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(status=202, payload=_job_response()),
        FakeAsyncResponse(payload={**_status_response(), "status": "succeeded"}),
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    result = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).raw.run(
            "heavy_metric",
            {"value": 3},
        )
    )

    assert result.kind == "table"
    assert result.outputs == {"result": [{"value": 6}]}


def test_async_client_submits_independently(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        posted_json: ClassVar[dict[str, Any] | None] = None

        def post(self, *_: object, **kwargs: object) -> FakeAsyncResponse:
            posted_json = kwargs.get("json")
            assert isinstance(posted_json, dict)
            type(self).posted_json = cast("dict[str, Any]", posted_json)
            return super().post()

    RecordingSession.responses = [
        FakeAsyncResponse(status=202, payload=_job_response())
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )

    response = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).raw.create(
            "heavy_metric",
            {"value": 3},
        )
    )

    assert response.job_id == "job-1"
    assert RecordingSession.posted_json == {
        "metric": "heavy_metric",
        "input": {"value": 3},
    }


def test_async_client_uses_admin_job_operations(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"method": "GET", "args": args, "kwargs": kwargs})
            return super().get(*args, **kwargs)

        def post(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append(
                {"method": "POST", "args": args, "kwargs": kwargs}
            )
            return super().post(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_job_list_response()),
        FakeAsyncResponse(payload=_job_detail_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAdminClient(
        "example.test",
        secure=False,
        timeout=12.0,
        admin_api_key="admin-secret",
    )

    jobs = client_runtime.run(
        client.jobs.list(limit=10, status="running", queue="heavy", offset=3)
    )
    detail = client_runtime.run(client.jobs.get("job-1"))

    assert RecordingSession.requests_seen == [
        {
            "method": "GET",
            "args": ("http://example.test/admin/jobs",),
            "kwargs": {
                "params": {
                    "limit": 10,
                    "status": "running",
                    "queue": "heavy",
                    "offset": 3,
                },
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "method": "GET",
            "args": ("http://example.test/admin/jobs/job-1",),
            "kwargs": {"headers": {"Authorization": "Bearer admin-secret"}},
        },
    ]
    assert [job.job_id for job in jobs.jobs] == ["job-1"]
    assert detail.snapshot.job_id == "job-1"
    assert detail.queue == "heavy"


def test_async_client_uses_observability_routes(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        urls: ClassVar[list[str]] = []
        headers: ClassVar[list[dict[str, str]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.urls.append(str(args[0]))
            headers = kwargs.get("headers")
            assert isinstance(headers, dict)
            self.headers.append(cast("dict[str, str]", headers))
            return super().get(*args, **kwargs)

    responses = [
        _liveness_response(),
        _readiness_response(),
        _admin_status_response(),
        _config_summary_response(),
        _catalog_summary_response(),
        _workers_response(),
        _worker_detail_response(),
        _queues_response(),
    ]
    RecordingSession.responses = [
        FakeAsyncResponse(payload=response) for response in responses
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAdminClient(
        "example.test",
        secure=False,
        admin_api_key="admin-secret",
    )

    liveness = client_runtime.run(client.health.liveness())
    readiness = client_runtime.run(client.health.readiness())
    status = client_runtime.run(client.status())
    config = client_runtime.run(client.config_summary())
    catalog = client_runtime.run(client.catalog.summary())
    workers = client_runtime.run(client.workers.list())
    worker = client_runtime.run(client.workers.get("interactive"))
    queues = client_runtime.run(client.queues.list())

    assert RecordingSession.urls == [
        "http://example.test/live",
        "http://example.test/ready",
        "http://example.test/admin/status",
        "http://example.test/admin/config-summary",
        "http://example.test/admin/catalog",
        "http://example.test/admin/workers",
        "http://example.test/admin/workers/interactive",
        "http://example.test/admin/queues",
    ]
    assert RecordingSession.headers[:2] == [{}, {}]
    assert (
        RecordingSession.headers[2:] == [{"Authorization": "Bearer admin-secret"}] * 6
    )
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert workers.workers[0].state == "busy"
    assert workers.workers[0].stale is False
    assert worker.current_job == "job-1"
    assert worker.heartbeat_age_seconds == pytest.approx(0.25)
    assert queues.queues[0].pending_depth_unknown is True


def test_async_client_exposes_structured_database_unavailability(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(
            status=503,
            payload=_redis_unavailable_response(),
            headers={"Retry-After": "5"},
        )
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        client_runtime.run(
            AsyncLyraClient("example.test", secure=False).raw.create("metric", {})
        )

    assert exc_info.value.code == "redis_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5


def test_async_client_uses_plugin_and_routing_routes(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().request(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_plugin_repo_list_response()),
        FakeAsyncResponse(payload=_plugin_routing_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", RecordingSession)
    admin = AsyncLyraAdminClient(
        "example.test/", secure=False, admin_api_key="admin-secret"
    )

    async def run_requests() -> tuple[Any, ...]:
        return (
            await admin.plugins.list(),
            await admin.routing.list(),
        )

    repos, routing = client_runtime.run(run_requests())
    assert RecordingSession.requests_seen == [
        {
            "args": ("GET", "http://example.test/admin/plugins"),
            "kwargs": {
                "params": None,
                "json": None,
                "timeout": aiohttp.ClientTimeout(
                    total=30.0, ceil_threshold=float("inf")
                ),
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("GET", "http://example.test/admin/plugin-routing"),
            "kwargs": {
                "params": None,
                "json": None,
                "timeout": aiohttp.ClientTimeout(
                    total=30.0, ceil_threshold=float("inf")
                ),
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
    ]
    assert repos.plugins[0].distribution == "lyra-smoke-plugin"
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}


def test_async_client_reports_operator_route_errors(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            assert isinstance(self, RecordingSession)
            del args, kwargs
            return FakeAsyncResponse(status=409, text="plugin disabled")

    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )

    with pytest.raises(
        DownloadError,
        match=r"Failed to list installed plugins\. HTTP 409: plugin disabled",
    ):
        client_runtime.run(
            AsyncLyraAdminClient("example.test", secure=False).plugins.list()
        )


def test_async_client_returns_location_selection_schema(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_data_types_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    response = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).catalog.data_types()
    )

    assert response.location_selection_schema == {"type": "object"}


def test_async_client_returns_metric_catalog(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_catalog_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    catalog = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).catalog.metrics()
    )

    assert len(catalog) == 1
    assert catalog[0].name == "accessibility_by_destination"


def test_async_client_returns_one_v5_metric(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    metric = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).catalog.metric(
            "accessibility_by_destination"
        )
    )

    assert metric.family.name == "accessibility_by_destination"


def test_async_client_rejects_invalid_data_type_response(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload={"location": []}),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    with pytest.raises(
        DownloadError, match="Failed to fetch data types: invalid JSON response"
    ):
        client_runtime.run(
            AsyncLyraClient("example.test", secure=False).catalog.data_types()
        )


def test_async_client_downloads_complete_job_result(
    client_runtime: ClientRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        urls: ClassVar[list[str]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            url = str(args[0])
            self.urls.append(url)
            return super().get(*args, **kwargs)

    def fake_aiofiles_open(path: Path, mode: str) -> FakeAsyncFile:
        assert mode == "wb"
        return FakeAsyncFile(path)

    RecordingSession.responses = [
        FakeAsyncResponse(
            headers={"content-type": "application/json"},
            chunks=[json.dumps(_result_response()).encode()],
        ),
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )
    monkeypatch.setattr("lyra.api.client.http.aiofiles.open", fake_aiofiles_open)
    output = tmp_path / "result.json"

    client_runtime.run(
        AsyncLyraClient("example.test", secure=False).results.download(
            "job-1",
            output,
        )
    )

    assert RecordingSession.urls == [
        "http://example.test/jobs/job-1/result/download",
    ]
    assert json.loads(output.read_bytes()) == _result_response()


def test_async_client_fetches_named_result_metadata(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.http.aiohttp.ClientSession", FakeSession)

    result = client_runtime.run(
        AsyncLyraClient("example.test", secure=False).results.get("job-1")
    )

    assert isinstance(result, TableJobResult)
    assert result.outputs == {"result": [{"value": 6}]}


def test_async_client_fetches_result_descriptor_from_raw_job_id(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().request(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_result_descriptor_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    )

    descriptor = client_runtime.run(client.results.inspect("job-1"))

    assert RecordingSession.requests_seen == [
        {
            "args": ("GET", "http://example.test/jobs/job-1/result/inspection"),
            "kwargs": {
                "params": None,
                "json": None,
                "timeout": aiohttp.ClientTimeout(
                    total=30.0, ceil_threshold=float("inf")
                ),
                "headers": {"Authorization": "Bearer agent-secret"},
            },
        }
    ]
    assert isinstance(descriptor, TerminalResultOutput)
    assert descriptor.outputs["result"].metadata is not None
    assert descriptor.completed_at is not None
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.outputs["result"].metadata.row_count == 1
    assert descriptor.completed_at.isoformat() == "2026-07-09T12:05:00+00:00"


def test_async_client_downloads_json_result_from_ref(
    client_runtime: ClientRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().get(*args, **kwargs)

    def fake_aiofiles_open(path: Path, mode: str) -> FakeAsyncFile:
        assert mode == "wb"
        return FakeAsyncFile(path)

    RecordingSession.responses = [
        FakeAsyncResponse(chunks=[json.dumps(_result_response()).encode()]),
    ]
    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        RecordingSession,
    )
    monkeypatch.setattr("lyra.api.client.http.aiofiles.open", fake_aiofiles_open)
    output = tmp_path / "result.json"

    client_runtime.run(
        AsyncLyraClient(
            "example.test",
            secure=False,
            agent_api_key="agent-secret",
        ).results.download(
            "lyra://results/job-1",
            output,
        )
    )

    assert RecordingSession.requests_seen == [
        {
            "args": ("http://example.test/jobs/job-1/result/download",),
            "kwargs": {
                "headers": {"Authorization": "Bearer agent-secret"},
                "timeout": aiohttp.ClientTimeout(
                    total=30.0, ceil_threshold=float("inf")
                ),
            },
        }
    ]
    assert json.loads(output.read_text()) == _result_response()


def test_async_client_reports_result_download_http_errors(
    client_runtime: ClientRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorSession(FakeSession):
        def get(self, *_: object, **__: object) -> FakeAsyncResponse:
            assert isinstance(self, ErrorSession)
            return FakeAsyncResponse(status=409, text="result is not a table")

    monkeypatch.setattr(
        "lyra.api.client.http.aiohttp.ClientSession",
        ErrorSession,
    )

    with pytest.raises(
        DownloadError,
        match=r"Failed to download result\. HTTP 409: result is not a table",
    ):
        client_runtime.run(
            AsyncLyraClient("example.test", secure=False).results.download(
                "job-1",
                tmp_path / "result.json",
            )
        )


def test_async_native_dataframe_projection_is_absent() -> None:
    assert not hasattr(LyraClient("unused.test").results, "dataframe")
    assert not hasattr(AsyncLyraClient("unused.test").results, "dataframe")


@pytest.mark.parametrize("revision", [None, " data-1 "])
def test_client_revision_request(revision: str | None) -> None:

    spec = create_job("root", {"value": 1}, cache_revision=revision)
    assert spec.json_body == {
        "metric": "root",
        "input": {"value": 1},
        **({"cache_revision": revision} if revision is not None else {}),
    }


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("edition", [None, "2025"])
def test_capability_client_workflow(
    client_runtime: ClientRuntime,
    monkeypatch: pytest.MonkeyPatch,
    edition: str | None,
    *,
    asynchronous: bool,
) -> None:

    seen: list[RequestSpec[Any]] = []
    payload: JsonObject = {
        "location": {"collection": "areas", "unit_type": "area", "ids": ["1"]}
    }

    def response(spec: RequestSpec[Any]) -> object:
        seen.append(spec)
        bodies = {
            "metrics": _metric_catalog_response(),
            "metrics/heavy_metric": _metric_response(),
            "jobs": _job_response(),
            "jobs/job-1": {**_status_response(), "status": "failed"},
            "jobs/job-1/result/inspection": inspection_of(
                TableJobResult.model_validate(
                    {**_result_response(), "outputs": {"result": []}}
                )
            ).model_dump(mode="json"),
        }
        return validate_response(
            spec,
            202 if spec.method == "POST" else 200,
            json.dumps(bodies[spec.path]),
            "application/json",
            None,
        )

    def sync_request(self: object, spec: RequestSpec[Any]) -> object:
        assert self is not None
        return response(spec)

    async def async_request(self: object, spec: RequestSpec[Any]) -> object:
        assert self is not None
        await asyncio.sleep(0)
        return response(spec)

    monkeypatch.setattr("lyra.api.client.sync._SyncTransport.request", sync_request)
    monkeypatch.setattr("lyra.api.client.async_._AsyncTransport.request", async_request)

    async def run_async() -> None:
        client = AsyncLyraClient("example.test")
        assert await client.catalog.metrics(query="area", limit=10, offset=2)
        await client.catalog.metric("heavy_metric", edition=edition)
        handle = await client.raw.submit(
            "heavy_metric", payload, edition=edition, cache_revision="fresh"
        )
        assert (await handle.status()).status == "failed"
        preview = await client.results.inspect(handle.job_id)
        assert isinstance(preview, TerminalResultOutput)
        assert preview.outputs["result"].preview == []

    if asynchronous:
        client_runtime.run(run_async())
    else:
        client = LyraClient("example.test")
        assert client.catalog.metrics(query="area", limit=10, offset=2)
        client.catalog.metric("heavy_metric", edition=edition)
        handle = client.raw.submit(
            "heavy_metric", payload, edition=edition, cache_revision="fresh"
        )
        assert handle.status().status == "failed"
        preview = client.results.inspect(handle.job_id)
        assert isinstance(preview, TerminalResultOutput)
        assert preview.outputs["result"].preview == []
    assert seen[0].params == {"query": "area", "limit": 10, "offset": 2}
    assert seen[1].params == ({"edition": edition} if edition else None)
    assert seen[2].json_body == {
        "metric": "heavy_metric",
        "input": payload,
        "cache_revision": "fresh",
        **({"edition": edition} if edition else {}),
    }
    assert seen[-1].authenticated


pytestmark = pytest.mark.usefixtures("client_runtime")
