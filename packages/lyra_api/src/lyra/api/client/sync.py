"""Synchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Generic,
    TypeVar,
    cast,
)

import requests
from lyra.api.client import endpoints
from lyra.api.client.base import BaseTransport, parse_result_ref
from lyra.api.client.bridge import PollingBridge
from lyra.api.client.endpoints import RequestSpec, validate_response
from lyra.api.client.http import request_json
from lyra.api.client.polling import PollingState
from lyra.api.client.results import (
    decode_tables,
    download_needs_text,
    successful_result,
    validate_download,
)
from lyra.api.exceptions import (
    DownloadError,
    JobNotReadyError,
    ServiceUnavailableError,
)
from lyra.sdk.models.job import (
    AdminJobDetail,
    JobCreateResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobProgress,
    JobStatusInfo,
    ResultDescriptor,
    TableJobResult,
    TerminalJobResult,
)

if TYPE_CHECKING:
    import os
    from collections.abc import Callable

    import geopandas as gpd
    import pandas as pd
    from lyra.api.options import RunOptions
    from lyra.sdk.models.admin import InstalledPluginListResponse, PluginRoutingResponse
    from lyra.sdk.models.data_types import DataTypesResponse
    from lyra.sdk.models.lookups import MetZoneCodeResponse
    from lyra.sdk.models.metric import MetricCatalogResponse, MetricInfo
    from lyra.sdk.models.observability import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        LivenessResponse,
        QueuesResponse,
        ReadinessResponse,
        WorkerDetail,
        WorkersResponse,
    )
    from lyra.sdk.types import JsonObject

_ResponseT = TypeVar("_ResponseT")
_SuccessResultT = TypeVar("_SuccessResultT", bound=TableJobResult)
SuccessfulJobResult = TableJobResult


class JobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result synchronously.

    Instances are returned by `LyraClient.raw.submit`; applications normally
    do not construct handles directly.

    Attributes:
        submission: The response returned when the job was submitted.

    """

    def __init__(
        self,
        client: _SyncTransport,
        submission: JobCreateResponse,
    ) -> None:
        """Initialize a handle for an existing job submission."""
        self._client = client
        self.submission = submission

    @property
    def job_id(self) -> str:
        """The submitted job's identifier."""
        return self.submission.job_id

    @property
    def metric(self) -> str:
        """The name of the submitted metric."""
        return self.submission.metric

    def status(self) -> JobStatusInfo:
        """Fetch the job's current lifecycle status.

        Returns:
            The latest status reported by the API.

        """
        return self._client.request(endpoints.get_job(self.job_id))

    def result(self) -> _SuccessResultT:
        """Fetch and return the job's successful terminal result.

        Returns:
            The complete named table result associated with the job.

        Failed jobs raise ``MetricRunError``.

        """
        result = self._client.request(endpoints.get_job_result(self.job_id))
        return cast("_SuccessResultT", successful_result(result))

    def wait(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float = 5.0,
        on_progress: Callable[[JobProgress], None] | None = None,
    ) -> _SuccessResultT:
        """Poll immediately, then periodically, until a terminal result is available.

        Progress callbacks receive changed snapshots. Callback failures propagate.
        Local timeout, cancellation, or interruption never cancels the remote job.
        The monotonic deadline bounds network observations and scheduled waiting,
        including terminal-result retrieval. It cannot preempt arbitrary user
        callbacks or synchronous CPU processing. Zero expires immediately;
        None permits unlimited overall waiting with bounded individual requests.

        Returns:
            The successful result; failed results raise MetricRunError.
        """
        state = PollingState(self.job_id, timeout, poll_interval)
        state.remaining()
        with PollingBridge() as bridge:
            terminal = False
            while True:
                try:
                    request = (
                        endpoints.get_job_result(self.job_id)
                        if terminal
                        else endpoints.get_job(self.job_id)
                    )
                    request_timeout = state.request_timeout(self._client.timeout)
                    response = bridge.observe(
                        self._client.observe(request, request_timeout=request_timeout),
                        request_timeout=request_timeout,
                    )
                    state.remaining()
                except JobNotReadyError as exc:
                    state.remaining()
                    response = exc.snapshot
                    terminal = False
                except (DownloadError, ServiceUnavailableError) as exc:
                    time.sleep(state.retry_delay(exc))
                    continue
                state.failures = 0
                if terminal:
                    return cast(
                        "_SuccessResultT",
                        successful_result(cast("TerminalJobResult", response)),
                    )
                snapshot = cast("JobStatusInfo", response)
                if state.changed(snapshot.progress) and on_progress is not None:
                    on_progress(cast("JobProgress", snapshot.progress))
                terminal = snapshot.status in {"succeeded", "failed"}
                if not terminal:
                    time.sleep(state.delay())


class _SyncTransport(BaseTransport):
    """Private synchronous HTTP implementation used by resource clients."""

    def request(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float | None = None
    ) -> _ResponseT:
        try:
            with requests.request(
                spec.method,
                self._http_url(spec.path),
                params=spec.params,
                json=spec.json_body,
                timeout=self.timeout if request_timeout is None else request_timeout,
                headers=self._auth_headers if spec.authenticated else self.headers,
            ) as response:
                return validate_response(
                    spec,
                    response.status_code,
                    response.text,
                    response.headers.get("content-type", ""),
                    response.headers.get("Retry-After"),
                )
        except requests.RequestException as exc:
            err = f"Failed to {spec.operation}: request error: {exc}"
            if spec.method == "POST" and spec.path == "jobs":
                err += (
                    " The job may have been accepted; "
                    "another submission may duplicate it."
                )
            raise DownloadError(
                err,
                retryable=isinstance(exc, (requests.ConnectionError, requests.Timeout))
                and not isinstance(exc, requests.exceptions.SSLError),
            ) from exc

    async def observe(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float
    ) -> _ResponseT:
        return await request_json(
            spec,
            self._http_url(spec.path),
            self._auth_headers if spec.authenticated else self.headers,
            request_timeout,
            exact_timeout=True,
        )

    def download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
    ) -> None:
        try:
            with requests.get(
                self._http_url(route),
                timeout=self.timeout,
                headers=self._auth_headers,
                stream=True,
            ) as response:
                if download_needs_text(response.status_code):
                    validate_download(
                        response.status_code,
                        response.text,
                        response.headers.get("Retry-After"),
                        operation=operation,
                    )
                with Path(path).open("wb") as file:
                    file.writelines(response.iter_content(chunk_size=65536))
        except requests.RequestException as exc:
            err = f"Failed to {operation}: request error: {exc}"
            raise DownloadError(err) from exc


class _HealthResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def liveness(self) -> LivenessResponse:
        return self._transport.request(endpoints.get_liveness())

    def readiness(self) -> ReadinessResponse:
        return self._transport.request(endpoints.get_readiness())


class _LookupsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def met_zone_code(self, name: str) -> MetZoneCodeResponse:
        return self._transport.request(endpoints.get_met_zone_code(name))


class _CatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def data_types(self) -> DataTypesResponse:
        return self._transport.request(endpoints.get_data_types())

    def metrics(self) -> MetricCatalogResponse:
        return self._transport.request(endpoints.get_metrics())

    def metric(self, name: str) -> MetricInfo:
        return self._transport.request(endpoints.get_metric(name))


class _JobsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def get(self, job_id: str) -> JobStatusInfo:
        return self._transport.request(endpoints.get_job(job_id))


class _ResultsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def get(self, job_id: str) -> TerminalJobResult:
        return self._transport.request(endpoints.get_job_result(job_id))

    def tables(self, ref: str) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch every named output as a native table.

        Returns:
            Tables keyed by their declared output names.
        """
        return decode_tables(self.get(parse_result_ref(ref)))

    def descriptor(self, ref: str) -> ResultDescriptor:
        return self._transport.request(
            endpoints.get_result_descriptor(parse_result_ref(ref))
        )

    def download(
        self,
        ref: str,
        path: str | os.PathLike[str],
    ) -> None:
        job_id = parse_result_ref(ref)
        self._transport.download(
            f"jobs/{job_id}/result/download", path, operation="download result"
        )


class _RawMetricsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def create(
        self,
        metric: str,
        arguments: JsonObject,
    ) -> JobCreateResponse:
        return self._transport.request(endpoints.create_job(metric, arguments))

    def submit(
        self,
        metric: str,
        arguments: JsonObject,
    ) -> JobHandle[SuccessfulJobResult]:
        return JobHandle(self._transport, self.create(metric, arguments))

    def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
    ) -> SuccessfulJobResult:
        wait_seconds = options.timeout if options is not None else None
        handle = self.submit(metric, arguments)
        return handle.wait(
            timeout=wait_seconds,
            poll_interval=options.poll_interval if options else 5.0,
        )

    def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        *,
        options: RunOptions | None = None,
    ) -> None:
        result = self.run(
            metric,
            arguments,
            options=options,
        )
        _ResultsResource(self._transport).download(result.job_id, path)


class _AdminJobsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus,
        queue: str,
        offset: int = 0,
    ) -> JobListResponse:
        return self._transport.request(
            endpoints.list_admin_jobs(
                limit=limit, status=status, queue=queue, offset=offset
            )
        )

    def get(self, job_id: str) -> AdminJobDetail:
        return self._transport.request(endpoints.get_admin_job(job_id))


class _AdminPluginsResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> InstalledPluginListResponse:
        return self._transport.request(endpoints.list_plugins())


class _AdminCatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def summary(self) -> CatalogSummaryResponse:
        return self._transport.request(endpoints.get_admin_catalog())


class _AdminWorkersResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> WorkersResponse:
        return self._transport.request(endpoints.get_admin_workers())

    def get(self, name: str) -> WorkerDetail:
        return self._transport.request(endpoints.get_admin_worker(name))


class _AdminQueuesResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> QueuesResponse:
        return self._transport.request(endpoints.get_admin_queues())


class _AdminRoutingResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def list(self) -> PluginRoutingResponse:
        return self._transport.request(endpoints.list_plugin_routing())


class LyraClient:
    """Access Lyra's consumer API with synchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`raw` to submit or run metrics with dictionary
    arguments, and :attr:`jobs` and :attr:`results` to observe existing jobs.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        agent_api_key: Bearer token for agent-protected job endpoints. Public
            catalog, lookup, and health endpoints do not require it.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Liveness and readiness endpoints.
        lookups: Public lookup endpoints.
        catalog: Metric and data-type discovery endpoints.
        jobs: Job status endpoints.
        results: Job result inspection and download endpoints.
        raw: Untyped metric submission and execution endpoints.

    Example:
        >>> client = LyraClient("lyra.example.com", agent_api_key="...")
        >>> metrics = client.catalog.metrics()

    """

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        agent_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize a synchronous consumer client and its endpoint resources."""
        transport = _SyncTransport(
            host,
            timeout,
            headers,
            api_key=agent_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Liveness and readiness endpoints."""
        self.lookups = _LookupsResource(transport)
        """Public lookup endpoints."""
        self.catalog = _CatalogResource(transport)
        """Metric and data-type discovery endpoints."""
        self.jobs = _JobsResource(transport)
        """Job status endpoints."""
        self.results = _ResultsResource(transport)
        """Job result inspection and download endpoints."""
        self.raw = _RawMetricsResource(transport)
        """Untyped metric submission and execution endpoints."""

    def result_tables(
        self, result_ref_or_job_id: str
    ) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch named tables by job ID or stable result reference.

        Spatial tables use WGS84. JSON does not retain native indices or dtypes.
        Failed jobs raise MetricRunError; unavailable or malformed results raise
        the same errors as other result retrieval operations.

        Returns:
            Every output keyed by its declared name, including empty tables.
        """
        return self.results.tables(result_ref_or_job_id)


class LyraAdminClient:
    """Access Lyra's administrator API with synchronous requests.

    Administrator credentials are intentionally isolated from `LyraClient`.
    This client exposes operational state and mutation endpoints, plus the public
    health checks, but does not expose consumer metric execution.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        admin_api_key: Bearer token for administrator endpoints.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Liveness and readiness endpoints.
        jobs: Administrative job listing and diagnostics endpoints.
        plugins: Installed plugin inspection endpoints.
        catalog: Administrative catalog summary endpoints.
        workers: Worker inspection endpoints.
        queues: Queue inspection endpoints.
        routing: Metric-to-queue routing endpoints.

    Example:
        >>> admin = LyraAdminClient("lyra.example.com", admin_api_key="...")
        >>> status = admin.status()

    """

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        admin_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize a synchronous administrator client and its resources."""
        transport = _SyncTransport(
            host,
            timeout,
            headers,
            api_key=admin_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Liveness and readiness endpoints."""
        self.jobs = _AdminJobsResource(transport)
        """Administrative job listing and diagnostics endpoints."""
        self.plugins = _AdminPluginsResource(transport)
        """Installed plugin inspection endpoints."""
        self.catalog = _AdminCatalogResource(transport)
        """Administrative catalog summary endpoints."""
        self.workers = _AdminWorkersResource(transport)
        """Worker inspection endpoints."""
        self.queues = _AdminQueuesResource(transport)
        """Queue inspection endpoints."""
        self.routing = _AdminRoutingResource(transport)
        """Metric-to-queue routing endpoints."""

    def status(self) -> AdminStatusResponse:
        """Fetch a summary of the running Lyra service.

        Returns:
            API, storage, catalog, queue, and worker configuration status.

        """
        return self._transport.request(endpoints.get_admin_status())

    def config_summary(self) -> ConfigSummaryResponse:
        """Fetch the effective non-secret service configuration.

        Returns:
            The API, queue, worker, job-store, and plugin path configuration.

        """
        return self._transport.request(endpoints.get_admin_config_summary())
