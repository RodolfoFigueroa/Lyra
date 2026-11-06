"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
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
from lyra.api.client.endpoints import RequestSpec
from lyra.api.client.http import HTTPTransport
from lyra.api.client.polling import PollingState, advance_polling
from lyra.api.client.results import (
    decode_tables,
    successful_result,
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
    TableJobResult,
    TerminalJobResult,
)

if TYPE_CHECKING:
    import os
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    import geopandas as gpd
    import pandas as pd
    from lyra.api.options import RunOptions, RunRequestOptions
    from lyra.sdk.discovery import FamilySummary, MetricInspection
    from lyra.sdk.models.admin import InstalledPluginListResponse, PluginRoutingResponse
    from lyra.sdk.models.data_types import DataTypesResponse
    from lyra.sdk.models.inspection import ResultInspection
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

_SuccessResultT = TypeVar("_SuccessResultT", bound=TableJobResult)
SuccessfulJobResult = TableJobResult


class AsyncJobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result asynchronously.

    Instances are returned by `AsyncLyraClient.raw.submit`; applications
    normally do not construct handles directly.

    Attributes:
        submission: The response returned when the job was submitted.

    """

    def __init__(
        self,
        client: _AsyncTransport,
        submission: JobCreateResponse,
    ) -> None:
        """Initialize a handle for an existing asynchronous job submission."""
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

    async def status(self) -> JobStatusInfo:
        """Fetch the job's current lifecycle status.

        Returns:
            The latest status reported by the API.

        """
        return await self._client.request(endpoints.get_job(self.job_id))

    async def result(self) -> _SuccessResultT:
        """Fetch and return the job's successful terminal result.

        Returns:
            The complete named table result associated with the job.

        Failed jobs raise ``MetricRunError``.

        """
        result = await self._client.request(endpoints.get_job_result(self.job_id))
        return cast("_SuccessResultT", successful_result(result))

    def wait(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float = 5.0,
        on_progress: Callable[[JobProgress], object] | None = None,
    ) -> Awaitable[JobStatusInfo]:
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

    async def _wait(
        self,
        state: PollingState,
        on_progress: Callable[[JobProgress], object] | None = None,
    ) -> JobStatusInfo:
        self._client.check_open()

        policy = state.observations()
        with closing(policy):
            action = advance_polling(policy)
            while not isinstance(action, JobStatusInfo):
                response = None
                if isinstance(action, RequestSpec):
                    try:
                        response = await self._client.request(
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
                        callback_result = on_progress(action)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                else:
                    await asyncio.sleep(action)
                action = advance_polling(policy, response)
            return action


class _AsyncTransport(HTTPTransport):
    """Asynchronous client transport."""


class _ClientLifetime:
    _transport: _AsyncTransport

    async def aclose(self) -> None:
        """Drain requests and close the session on its owning event loop."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        """Return this open client on its owning event loop."""
        self._transport.check_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on context exit."""
        await self.aclose()


class _HealthResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def liveness(self) -> LivenessResponse:
        return await self._transport.request(endpoints.get_liveness())

    async def readiness(self) -> ReadinessResponse:
        return await self._transport.request(endpoints.get_readiness())


class _CatalogResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def data_types(self) -> DataTypesResponse:
        return await self._transport.request(endpoints.get_data_types())

    async def metrics(
        self, *, query: str = "", offset: int = 0, limit: int = 50
    ) -> list[FamilySummary]:
        return await self._transport.request(
            endpoints.get_metrics(query=query, offset=offset, limit=limit)
        )

    async def metric(
        self, name: str, *, edition: str | None = None
    ) -> MetricInspection:
        return await self._transport.request(
            endpoints.get_metric(name, edition=edition)
        )


class _JobsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> JobStatusInfo:
        return await self._transport.request(endpoints.get_job(job_id))


class _ResultsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> TerminalJobResult:
        return await self._transport.request(endpoints.get_job_result(job_id))

    async def tables(self, ref: str) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch every named output as a native table.

        Returns:
            Tables keyed by their declared output names.
        """
        return decode_tables(await self.get(parse_result_ref(ref)))

    async def inspect(self, ref: str) -> ResultInspection:
        return await self._transport.request(
            endpoints.get_result_inspection(parse_result_ref(ref))
        )

    async def download(
        self,
        ref: str,
        path: str | os.PathLike[str],
    ) -> None:
        job_id = parse_result_ref(ref)
        await self._transport.download(
            f"jobs/{job_id}/result/download", path, operation="download result"
        )


class _RawMetricsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def create(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> JobCreateResponse:
        return await self._transport.request(
            endpoints.create_job(
                metric, arguments, edition=edition, cache_revision=cache_revision
            )
        )

    async def submit(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> AsyncJobHandle[SuccessfulJobResult]:
        return AsyncJobHandle(
            self._transport,
            await self.create(
                metric, arguments, edition=edition, cache_revision=cache_revision
            ),
        )

    async def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
        edition: str | None = None,
        cache_revision: str | None = None,
    ) -> SuccessfulJobResult:
        handle = await self.submit(
            metric, arguments, edition=edition, cache_revision=cache_revision
        )
        state = PollingState(
            handle.job_id,
            options.timeout if options else None,
            options.poll_interval if options else 5.0,
        )
        await handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        try:
            result = await self._transport.request(
                endpoints.get_job_result(handle.job_id),
                request_timeout=state.request_timeout(self._transport.timeout),
            )
        except DownloadError:
            state.remaining()
            raise
        state.remaining()
        return successful_result(result)

    async def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        **kwargs: Unpack[RunRequestOptions],
    ) -> None:
        options = kwargs.get("options")
        handle = await self.submit(
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
        await handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        try:
            await self._transport.download(
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
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus,
        queue: str,
        offset: int = 0,
    ) -> JobListResponse:
        return await self._transport.request(
            endpoints.list_admin_jobs(
                limit=limit, status=status, queue=queue, offset=offset
            )
        )

    async def get(self, job_id: str) -> AdminJobDetail:
        return await self._transport.request(endpoints.get_admin_job(job_id))


class _AdminPluginsResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> InstalledPluginListResponse:
        return await self._transport.request(endpoints.list_plugins())


class _AdminCatalogResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def summary(self) -> CatalogSummaryResponse:
        return await self._transport.request(endpoints.get_admin_catalog())


class _AdminWorkersResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> WorkersResponse:
        return await self._transport.request(endpoints.get_admin_workers())

    async def get(self, name: str) -> WorkerDetail:
        return await self._transport.request(endpoints.get_admin_worker(name))


class _AdminQueuesResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> QueuesResponse:
        return await self._transport.request(endpoints.get_admin_queues())


class _AdminRoutingResource:
    def __init__(self, transport: _AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> PluginRoutingResponse:
        return await self._transport.request(endpoints.list_plugin_routing())


class AsyncLyraClient(_ClientLifetime):
    """Access Lyra's consumer API with asynchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`raw` to submit or run metrics with dictionary
    arguments, and :attr:`jobs` and :attr:`results` to observe existing jobs.

    Use ``async with`` or await ``aclose()`` on the owning event loop.
    Concurrent requests on that loop share one session.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Total HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        agent_api_key: Bearer token for agent-protected job endpoints. Public
            catalog, lookup, and health endpoints do not require it.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        catalog: Asynchronous metric and data-type discovery endpoints.
        jobs: Asynchronous job status endpoints.
        results: Asynchronous job result inspection and download endpoints.
        raw: Asynchronous untyped metric submission and execution endpoints.

    Example:
        >>> async with AsyncLyraClient(
        ...     "lyra.example.com", agent_api_key="..."
        ... ) as client:
        ...     metrics = await client.catalog.metrics()

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
        """Initialize an asynchronous consumer client and its resources."""
        transport = _AsyncTransport(
            host,
            timeout,
            headers,
            api_key=agent_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Asynchronous liveness and readiness endpoints."""
        self.catalog = _CatalogResource(transport)
        """Asynchronous metric and data-type discovery endpoints."""
        self.jobs = _JobsResource(transport)
        """Asynchronous job status endpoints."""
        self.results = _ResultsResource(transport)
        """Asynchronous job result inspection and download endpoints."""
        self.raw = _RawMetricsResource(transport)
        """Asynchronous untyped metric submission and execution endpoints."""

    async def result_tables(
        self, result_ref_or_job_id: str
    ) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch named tables by job ID or stable result reference.

        Spatial tables use WGS84. JSON does not retain native indices or dtypes.
        Failed jobs raise MetricRunError; unavailable or malformed results raise
        the same errors as other result retrieval operations.

        Returns:
            Every output keyed by its declared name, including empty tables.
        """
        return await self.results.tables(result_ref_or_job_id)


class AsyncLyraAdminClient(_ClientLifetime):
    """Access Lyra's administrator API with asynchronous requests.

    Administrator credentials are intentionally isolated from
    `AsyncLyraClient`. This client exposes operational state and mutation
    endpoints, plus the public health checks, but does not expose consumer metric
    execution.

    Use ``async with`` or await ``aclose()`` on the owning event loop.
    Concurrent requests on that loop share one session.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Total HTTP request timeout in seconds.
        headers: Additional headers included with every request.
        admin_api_key: Bearer token for administrator endpoints.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        jobs: Asynchronous administrative job listing and diagnostics endpoints.
        plugins: Asynchronous installed plugin inspection endpoints.
        catalog: Asynchronous administrative catalog summary endpoints.
        workers: Asynchronous worker inspection endpoints.
        queues: Asynchronous queue inspection endpoints.
        routing: Asynchronous metric-to-queue routing endpoints.

    Example:
        >>> async with AsyncLyraAdminClient(
        ...     "lyra.example.com", admin_api_key="..."
        ... ) as admin:
        ...     status = await admin.status()

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
        """Initialize an asynchronous administrator client and its resources."""
        transport = _AsyncTransport(
            host,
            timeout,
            headers,
            api_key=admin_api_key,
            secure=secure,
        )
        self._transport = transport
        self.health = _HealthResource(transport)
        """Asynchronous liveness and readiness endpoints."""
        self.jobs = _AdminJobsResource(transport)
        """Asynchronous administrative job listing and diagnostics endpoints."""
        self.plugins = _AdminPluginsResource(transport)
        """Asynchronous installed plugin inspection endpoints."""
        self.catalog = _AdminCatalogResource(transport)
        """Asynchronous administrative catalog summary endpoints."""
        self.workers = _AdminWorkersResource(transport)
        """Asynchronous worker inspection endpoints."""
        self.queues = _AdminQueuesResource(transport)
        """Asynchronous queue inspection endpoints."""
        self.routing = _AdminRoutingResource(transport)
        """Asynchronous metric-to-queue routing endpoints."""

    async def status(self) -> AdminStatusResponse:
        """Fetch a summary of the running Lyra service.

        Returns:
            API, storage, catalog, queue, and worker configuration status.

        """
        return await self._transport.request(endpoints.get_admin_status())

    async def config_summary(self) -> ConfigSummaryResponse:
        """Fetch the effective non-secret service configuration.

        Returns:
            The API, queue, worker, job-store, and plugin path configuration.

        """
        return await self._transport.request(endpoints.get_admin_config_summary())
