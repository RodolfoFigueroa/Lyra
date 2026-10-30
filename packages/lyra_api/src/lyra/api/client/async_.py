"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
from typing import (
    TYPE_CHECKING,
    Generic,
    TypeVar,
    cast,
)

import aiofiles
import aiohttp
from lyra.api.client import endpoints
from lyra.api.client.base import BaseTransport, parse_result_ref
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
    from collections.abc import Awaitable, Callable

    import geopandas as gpd
    import pandas as pd
    from lyra.api.client.endpoints import RequestSpec
    from lyra.api.options import RunOptions
    from lyra.sdk.models.admin import InstalledPluginListResponse, PluginRoutingResponse
    from lyra.sdk.models.data_types import DataTypesResponse
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


def _consume_exception(future: asyncio.Future[_ResponseT]) -> None:
    if not future.cancelled():
        future.exception()


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
    ) -> Awaitable[_SuccessResultT]:
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
        return self._wait(
            wait_seconds=timeout, poll_interval=poll_interval, on_progress=on_progress
        )

    async def _wait(
        self,
        *,
        wait_seconds: float | None,
        poll_interval: float,
        on_progress: Callable[[JobProgress], object] | None,
    ) -> _SuccessResultT:
        state = PollingState(self.job_id, wait_seconds, poll_interval)
        terminal = False
        while True:
            try:
                request = (
                    endpoints.get_job_result(self.job_id)
                    if terminal
                    else endpoints.get_job(self.job_id)
                )
                response = await self._client.observe(
                    request, request_timeout=state.request_timeout(self._client.timeout)
                )
                state.remaining()
            except JobNotReadyError as exc:
                state.remaining()
                response = exc.snapshot
                terminal = False
            except (DownloadError, ServiceUnavailableError) as exc:
                await asyncio.sleep(state.retry_delay(exc))
                continue
            state.failures = 0
            if terminal:
                return cast(
                    "_SuccessResultT",
                    successful_result(cast("TerminalJobResult", response)),
                )
            snapshot = cast("JobStatusInfo", response)
            if state.changed(snapshot.progress) and on_progress is not None:
                callback_result = on_progress(cast("JobProgress", snapshot.progress))
                if inspect.isawaitable(callback_result):
                    await callback_result
            terminal = snapshot.status in {"succeeded", "failed"}
            if not terminal:
                await asyncio.sleep(state.delay())


class _AsyncTransport(BaseTransport):
    """Private asynchronous HTTP implementation used by resource clients."""

    async def request(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float | None = None
    ) -> _ResponseT:
        return await request_json(
            spec,
            self._http_url(spec.path),
            self._auth_headers if spec.authenticated else self.headers,
            self.timeout if request_timeout is None else request_timeout,
            exact_timeout=request_timeout is not None,
        )

    async def observe(
        self, spec: RequestSpec[_ResponseT], *, request_timeout: float
    ) -> _ResponseT:
        return await self.request(spec, request_timeout=request_timeout)

    async def download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
    ) -> None:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._http_url(route), headers=self._auth_headers
                ) as response,
            ):
                if download_needs_text(response.status):
                    validate_download(
                        response.status,
                        await response.text(),
                        response.headers.get("Retry-After"),
                        operation=operation,
                    )
                async with aiofiles.open(path, "wb") as file:
                    async for chunk in response.content.iter_chunked(65536):
                        await file.write(chunk)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            err = f"Failed to {operation}: request error: {exc}"
            raise DownloadError(err) from exc


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

    async def metrics(self) -> MetricCatalogResponse:
        return await self._transport.request(endpoints.get_metrics())

    async def metric(self, name: str) -> MetricInfo:
        return await self._transport.request(endpoints.get_metric(name))


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

    async def descriptor(self, ref: str) -> ResultDescriptor:
        return await self._transport.request(
            endpoints.get_result_descriptor(parse_result_ref(ref))
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
        cache_revision: str | None = None,
    ) -> JobCreateResponse:
        return await self._transport.request(
            endpoints.create_job(metric, arguments, cache_revision=cache_revision)
        )

    async def submit(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        cache_revision: str | None = None,
    ) -> AsyncJobHandle[SuccessfulJobResult]:
        return AsyncJobHandle(
            self._transport,
            await self.create(metric, arguments, cache_revision=cache_revision),
        )

    async def run(
        self,
        metric: str,
        arguments: JsonObject,
        *,
        options: RunOptions | None = None,
        cache_revision: str | None = None,
    ) -> SuccessfulJobResult:
        wait_seconds = options.timeout if options is not None else None
        handle = await self.submit(metric, arguments, cache_revision=cache_revision)
        return await handle.wait(
            timeout=wait_seconds,
            poll_interval=options.poll_interval if options else 5.0,
        )

    async def run_to_file(
        self,
        metric: str,
        arguments: JsonObject,
        path: str | os.PathLike[str],
        *,
        options: RunOptions | None = None,
        cache_revision: str | None = None,
    ) -> None:
        result = await self.run(
            metric,
            arguments,
            options=options,
            cache_revision=cache_revision,
        )
        await _ResultsResource(self._transport).download(result.job_id, path)


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


class AsyncLyraClient:
    """Access Lyra's consumer API with asynchronous requests.

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
        health: Asynchronous liveness and readiness endpoints.
        catalog: Asynchronous metric and data-type discovery endpoints.
        jobs: Asynchronous job status endpoints.
        results: Asynchronous job result inspection and download endpoints.
        raw: Asynchronous untyped metric submission and execution endpoints.

    Example:
        >>> client = AsyncLyraClient("lyra.example.com", agent_api_key="...")
        >>> metrics = await client.catalog.metrics()

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


class AsyncLyraAdminClient:
    """Access Lyra's administrator API with asynchronous requests.

    Administrator credentials are intentionally isolated from
    `AsyncLyraClient`. This client exposes operational state and mutation
    endpoints, plus the public health checks, but does not expose consumer metric
    execution.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: Default HTTP request timeout in seconds.
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
        >>> admin = AsyncLyraAdminClient(
        ...     "lyra.example.com", admin_api_key="..."
        ... )
        >>> status = await admin.status()

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
