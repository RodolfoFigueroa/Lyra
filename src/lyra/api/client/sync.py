"""Synchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import time
from contextlib import closing
from typing import (
    TYPE_CHECKING,
    Generic,
    Self,
    TypeVar,
    Unpack,
    cast,
)

from lyra.api.client import endpoints
from lyra.api.client.base import parse_result_ref
from lyra.api.client.bridge import TransportBridge
from lyra.api.client.endpoints import RequestSpec
from lyra.api.client.http import HTTPTransport, transport_error
from lyra.api.client.polling import PollingState, advance_polling
from lyra.api.client.results import successful_result
from lyra.api.exceptions import (
    DownloadError,
    JobNotReadyError,
    ServiceUnavailableError,
)
from lyra.contracts.jobs import (
    AdminJobDetail,
    JobCreateResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobProgress,
    JobStatusInfo,
    TableJobResult,
    TerminalJobResult,
)

if TYPE_CHECKING:
    import os
    from collections.abc import Callable
    from types import TracebackType

    import geopandas as gpd
    import pandas as pd

    from lyra.api.options import RunOptions, RunRequestOptions
    from lyra.contracts.admin import InstalledPluginListResponse, PluginRoutingResponse
    from lyra.contracts.data_types import DataTypesResponse
    from lyra.contracts.discovery import FamilySummary, MetricInspection
    from lyra.contracts.inspection import ResultInspection
    from lyra.contracts.observability import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        LivenessResponse,
        QueuesResponse,
        ReadinessResponse,
        WorkerDetail,
        WorkersResponse,
    )
    from lyra.contracts.types import JsonObject

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
    ) -> JobStatusInfo:
        """Wait for completion without fetching output data.

        Returns:
            Successful terminal status; failed jobs raise MetricRunError.

        The deadline bounds status requests and scheduled waiting. Local timeout
        or cancellation does not cancel the remote job. Progress callbacks receive
        changed snapshots; callback failures propagate.
        """
        return self._wait(
            PollingState(self.job_id, timeout, poll_interval), on_progress
        )

    def _wait(
        self,
        state: PollingState,
        on_progress: Callable[[JobProgress], None] | None = None,
    ) -> JobStatusInfo:
        self._client.check_open()
        policy = state.observations()
        with closing(policy):
            action = advance_polling(policy)
            while not isinstance(action, JobStatusInfo):
                response = None
                if isinstance(action, RequestSpec):
                    try:
                        response = self._client.request(
                            action,
                            request_timeout=state.request_timeout(self._client.timeout),
                        )
                    except (
                        JobNotReadyError,
                        DownloadError,
                        ServiceUnavailableError,
                    ) as exc:
                        action = advance_polling(policy, error=exc)
                        continue
                elif isinstance(action, JobProgress):
                    if on_progress is not None:
                        on_progress(action)

                else:
                    time.sleep(action)
                action = advance_polling(policy, response)
            return action


class _SyncTransport:
    """Synchronous adapter for the client-owned HTTP transport."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        self._http = HTTPTransport(
            host, timeout, headers, api_key=api_key, secure=secure
        )
        self.timeout = timeout
        self._bridge = TransportBridge(self._http)

    def check_open(self) -> None:
        self._bridge.check_open()

    def close(self) -> None:
        self._bridge.close()

    def request(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float | None = None
    ) -> _ResponseT:
        seconds = self.timeout if request_timeout is None else request_timeout
        try:
            return self._bridge.call(
                self._http.request(spec, request_timeout=seconds), seconds=seconds
            )
        except TimeoutError as exc:
            raise transport_error(
                spec.operation,
                exc,
                submission=spec.method == "POST" and spec.path == "jobs",
            ) from exc

    def download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
        request_timeout: float | None = None,
    ) -> None:
        try:
            self._bridge.call(
                self._http.download(
                    route, path, operation=operation, request_timeout=request_timeout
                ),
                seconds=self.timeout if request_timeout is None else request_timeout,
            )
        except TimeoutError as exc:
            raise transport_error(operation, exc) from exc


class _ClientLifetime:
    _transport: _SyncTransport

    def close(self) -> None:
        """Close the session and join the client's background thread."""
        self._transport.close()

    def __enter__(self) -> Self:
        """Return this open client."""
        self._transport.check_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on context exit."""
        self.close()


class _HealthResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def liveness(self) -> LivenessResponse:
        return self._transport.request(endpoints.get_liveness())

    def readiness(self) -> ReadinessResponse:
        return self._transport.request(endpoints.get_readiness())


class _CatalogResource:
    def __init__(self, transport: _SyncTransport) -> None:
        self._transport = transport

    def data_types(self) -> DataTypesResponse:
        return self._transport.request(endpoints.get_data_types())

    def metrics(
        self, *, query: str = "", offset: int = 0, limit: int = 50
    ) -> list[FamilySummary]:
        return self._transport.request(
            endpoints.get_metrics(query=query, offset=offset, limit=limit)
        )

    def metric(self, name: str, *, edition: str | None = None) -> MetricInspection:
        return self._transport.request(endpoints.get_metric(name, edition=edition))


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
        result = successful_result(self.get(parse_result_ref(ref)))
        # Load the private table adapter only when optional tables are requested.
        from lyra.api.client._tables import (  # ruff: ignore[import-outside-top-level] - optional private adapter
            decode_tables,
        )

        return decode_tables(result)

    def inspect(self, ref: str) -> ResultInspection:
        return self._transport.request(
            endpoints.get_result_inspection(parse_result_ref(ref))
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
        *,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> JobCreateResponse:
        return self._transport.request(
            endpoints.create_job(
                metric, arguments, edition=edition, cache_revision=cache_revision
            )
        )

    def submit(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> JobHandle[SuccessfulJobResult]:
        return JobHandle(
            self._transport,
            self.create(
                metric, arguments, edition=edition, cache_revision=cache_revision
            ),
        )

    def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> SuccessfulJobResult:
        handle = self.submit(
            metric, arguments, edition=edition, cache_revision=cache_revision
        )
        state = PollingState(
            handle.job_id,
            options.timeout if options else None,
            options.poll_interval if options else 5.0,
        )
        handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        try:
            result = self._transport.request(
                endpoints.get_job_result(handle.job_id),
                request_timeout=state.request_timeout(self._transport.timeout),
            )
        except DownloadError:
            state.remaining()
            raise
        state.remaining()
        return successful_result(result)

    def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        **kwargs: Unpack[RunRequestOptions],
    ) -> None:
        options = kwargs.get("options")
        handle = self.submit(
            metric,
            arguments,
            edition=kwargs.get("edition"),
            cache_revision=kwargs.get("cache_revision"),
        )
        state = PollingState(
            handle.job_id,
            options.timeout if options else None,
            options.poll_interval if options else 5.0,
        )
        handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        try:
            self._transport.download(
                f"jobs/{handle.job_id}/result/download",
                path,
                operation="download result",
                request_timeout=state.request_timeout(self._transport.timeout),
            )
        except DownloadError:
            state.remaining()
            raise
        state.remaining()


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


class LyraClient(_ClientLifetime):
    """Access Lyra's consumer API with synchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`raw` to submit or run metrics with dictionary
    arguments, and :attr:`jobs` and :attr:`results` to observe existing jobs.

    Use a context manager or call ``close()`` to release the session and thread.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Total HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        agent_api_key: Bearer token for agent-protected job endpoints. Public
            catalog, lookup, and health endpoints do not require it.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Liveness and readiness endpoints.
        catalog: Metric and data-type discovery endpoints.
        jobs: Job status endpoints.
        results: Job result inspection and download endpoints.
        raw: Untyped metric submission and execution endpoints.

    Example:
        >>> with LyraClient("lyra.example.com", agent_api_key="...") as client:
        ...     metrics = client.catalog.metrics()

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


class LyraAdminClient(_ClientLifetime):
    """Access Lyra's administrator API with synchronous requests.

    Administrator credentials are intentionally isolated from `LyraClient`.
    This client exposes operational state and mutation endpoints, plus the public
    health checks, but does not expose consumer metric execution.

    Use a context manager or call ``close()`` to release the session and thread.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Total HTTP request timeout in seconds.
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
        >>> with LyraAdminClient("lyra.example.com", admin_api_key="...") as admin:
        ...     status = admin.status()

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
