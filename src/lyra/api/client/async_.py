"""Asynchronous consumer and administrator clients for the Lyra HTTP API."""

from __future__ import annotations

import asyncio
import inspect
from importlib import import_module
from typing import (
    TYPE_CHECKING,
    Generic,
    Self,
    TypeVar,
    Unpack,
    cast,
)

from lyra.api.client import endpoints, submission
from lyra.api.client.httpx import AsyncHTTPTransport
from lyra.api.client.polling import PollingState
from lyra.api.client.results import successful_result, successful_status
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
from lyra.contracts.retrieval_paths import report_download_path

if TYPE_CHECKING:
    import os
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path
    from types import TracebackType

    import geopandas as gpd
    import httpx
    import pandas as pd

    from lyra.api.options import RunRequestOptions
    from lyra.contracts.admin import InstalledPluginListResponse
    from lyra.contracts.data_types import DataTypesResponse
    from lyra.contracts.discovery import MetricInspection, MetricSummary
    from lyra.contracts.inspection import ResultInspection
    from lyra.contracts.observability import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        LivenessResponse,
        QueueSummary,
        ReadinessResponse,
        WorkerDetail,
        WorkersResponse,
    )
    from lyra.contracts.reports import ExecutionReport, ExecutionReportInspection
    from lyra.contracts.results import ResultDescription, ResultRowPage
    from lyra.contracts.types import JsonObject
    from lyra.data.interventions import Intervention

_SuccessResultT = TypeVar("_SuccessResultT", bound=TableJobResult)
SuccessfulJobResult = TableJobResult


class AsyncJobHandle(Generic[_SuccessResultT]):
    """Observe a submitted job and retrieve its successful result asynchronously.

    Instances are returned by `AsyncLyraClient.jobs.submit`; applications
    normally do not construct handles directly.

    Attributes:
        submission: The response returned when the job was submitted.

    """

    def __init__(
        self,
        client: AsyncHTTPTransport,
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

        The deadline is checked between operations; requests and cleanup may
        finish after it expires. Local timeout or cancellation does not cancel the
        remote job. Progress callbacks receive changed snapshots; callback
        failures propagate.
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
        while True:
            state.remaining()
            try:
                snapshot = await self._client.request(endpoints.get_job(self.job_id))
            except JobNotReadyError as exc:
                snapshot = exc.snapshot
            except (DownloadError, ServiceUnavailableError) as exc:
                await asyncio.sleep(state.retry_delay(exc))
                continue
            state.remaining()
            state.failures = 0
            if (
                state.changed(snapshot.progress)
                and on_progress is not None
                and snapshot.progress is not None
            ):
                callback_result = on_progress(snapshot.progress)
                if inspect.isawaitable(callback_result):
                    await callback_result
            state.remaining()
            if snapshot.status in {"succeeded", "failed"}:
                return successful_status(snapshot)
            await asyncio.sleep(state.delay())


class _ClientLifetime:
    _transport: AsyncHTTPTransport

    async def aclose(self) -> None:
        """Close the connection pool after caller-owned operations settle."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        """Return this open client."""
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
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def liveness(self) -> LivenessResponse:
        return await self._transport.request(endpoints.get_liveness())

    async def readiness(self) -> ReadinessResponse:
        return await self._transport.request(endpoints.get_readiness())


class _CatalogResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def data_types(self) -> DataTypesResponse:
        return await self._transport.request(endpoints.get_data_types())

    async def metrics(
        self, *, query: str = "", offset: int = 0, limit: int = 50
    ) -> list[MetricSummary]:
        return await self._transport.request(
            endpoints.get_metrics(query=query, offset=offset, limit=limit)
        )

    async def metric(self, name: str) -> MetricInspection:
        return await self._transport.request(endpoints.get_metric(name))


class _ReportsResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> ExecutionReport:
        """Return the complete validated report from its JSON download."""
        return await self._transport.request(endpoints.get_report(job_id))

    async def inspect(self, job_id: str) -> ExecutionReportInspection:
        """Return the whole report or a bounded preview with a download URL."""
        return await self._transport.request(endpoints.get_report_inspection(job_id))

    async def download(self, job_id: str, path: str | os.PathLike[str]) -> None:
        """Stream complete JSON, replacing the destination only after success."""
        await self._transport.download(
            report_download_path(job_id),
            path,
            operation="download report",
            atomic=True,
        )


class _ResultsResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def describe(self, job_id: str) -> ResultDescription:
        """Return complete retained metadata, following a preview link when needed."""
        inspection = await self._transport.request(
            endpoints.get_result_description(job_id)
        )
        if inspection.kind == "complete":
            return inspection.description
        return await self._transport.request(
            endpoints.download_result_description(inspection.download_url)
        )

    async def get(self, job_id: str) -> TerminalJobResult:
        return await self._transport.request(endpoints.get_job_result(job_id))

    async def rows(
        self,
        job_id: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ResultRowPage:
        """Return a bounded page, preserving decimal integer and WKT strings."""
        return await self._transport.request(
            endpoints.get_result_rows(job_id, output_name, limit, cursor)
        )

    async def table(
        self, job_id: str, output_name: str
    ) -> pd.DataFrame | gpd.GeoDataFrame:
        """Return an independent native frame from the retained Arrow stream.

        Nullable scalar types, geometry, and CRS come from retained Arrow
        metadata. The frame remains usable after client closure.
        """
        return await self._native_table(
            endpoints.get_table_download(job_id, output_name)
        )

    async def export_geoparquet(
        self, job_id: str, output_name: str, path: str | Path
    ) -> None:
        """Export a retained spatial output to an atomically replaced local file."""
        frame = await self.table(job_id, output_name)
        description = await self.describe(job_id)
        geoparquet = import_module("lyra.data.geoparquet")
        geoparquet.export_retained_geoparquet(
            frame, description, "metric_output", output_name, path
        )

    async def export_location_input_geoparquet(
        self, job_id: str, input_name: str, path: str | Path
    ) -> None:
        """Export a retained spatial input to an atomically replaced local file."""
        frame = await self.location_input(job_id, input_name)
        description = await self.describe(job_id)
        geoparquet = import_module("lyra.data.geoparquet")
        geoparquet.export_retained_geoparquet(
            frame, description, "location_input", input_name, path
        )

    async def _native_table(
        self, spec: endpoints.DownloadSpec
    ) -> pd.DataFrame | gpd.GeoDataFrame:
        content = await self._transport.read_bytes(spec)
        from lyra.api.client._arrow import (  # ruff: ignore[import-outside-top-level] - optional private adapter
            decode_table,
        )

        return decode_table(content)

    async def download_table(
        self, job_id: str, output_name: str, path: str | os.PathLike[str]
    ) -> None:
        """Atomically save unchanged IPC stream bytes, regardless of extension.

        The parent directory must exist. Failure or cancellation preserves any
        existing destination and removes the temporary file.
        """
        spec = endpoints.get_table_download(job_id, output_name)
        await self._transport.download(
            spec.path,
            path,
            operation=spec.operation,
            atomic=True,
            content_type=spec.content_type,
        )

    async def location_input(self, job_id: str, input_name: str) -> gpd.GeoDataFrame:
        """Return an independent retained input with nullable types, geometry and CRS.

        Known empty inputs retain their schema. The complete logical selection
        remains usable after closing the client.
        """
        return cast(
            "gpd.GeoDataFrame",
            await self._native_table(
                endpoints.get_location_input_download(job_id, input_name)
            ),
        )

    async def location_input_rows(
        self,
        job_id: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ResultRowPage:
        """Return a bounded page of the retained logical input selection."""
        return await self._transport.request(
            endpoints.get_location_input_rows(job_id, input_name, limit, cursor)
        )

    async def download_location_input(
        self, job_id: str, input_name: str, destination: str | os.PathLike[str]
    ) -> None:
        """Atomically save the input as Arrow IPC and return None.

        The parent directory must exist. Failure or cancellation preserves the
        destination and removes the temporary file.
        """
        spec = endpoints.get_location_input_download(job_id, input_name)
        await self._transport.download(
            spec.path,
            destination,
            operation=spec.operation,
            atomic=True,
            content_type=spec.content_type,
        )

    async def tables(self, job_id: str) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch every named output as a native table.

        Returns:
            Tables keyed by their declared output names.
        """
        result = successful_result(await self.get(job_id))
        # Load the private table adapter only when optional tables are requested.
        from lyra.api.client._tables import (  # ruff: ignore[import-outside-top-level] - optional private adapter
            decode_tables,
        )

        return decode_tables(result)

    async def inspect(self, job_id: str) -> ResultInspection:
        return await self._transport.request(endpoints.get_result_inspection(job_id))

    async def download(
        self,
        job_id: str,
        path: str | os.PathLike[str],
    ) -> None:
        await self._transport.download(
            endpoints.result_download_path(job_id),
            path,
            operation="download result",
            atomic=False,
        )


class _JobsResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def get(self, job_id: str) -> JobStatusInfo:
        return await self._transport.request(endpoints.get_job(job_id))

    async def create(
        self,
        metric: str,
        *,
        inputs: JsonObject | None = None,
        parameters: JsonObject | None = None,
        interventions: Sequence[Intervention] | None = None,
    ) -> JobCreateResponse:
        request = endpoints.create_job(metric, inputs=inputs, parameters=parameters)
        changes = submission.capture(interventions)
        if changes:
            discovery = (
                await self._transport.request(endpoints.get_metric(metric))
                if any(change.add is not None for change in changes)
                else None
            )
            request = submission.with_interventions(request, changes, discovery)
        return await self._transport.request(request)

    async def submit(
        self,
        metric: str,
        *,
        inputs: JsonObject | None = None,
        parameters: JsonObject | None = None,
        interventions: Sequence[Intervention] | None = None,
    ) -> AsyncJobHandle[SuccessfulJobResult]:
        return AsyncJobHandle(
            self._transport,
            await self.create(
                metric,
                inputs=inputs,
                parameters=parameters,
                interventions=interventions,
            ),
        )

    async def run(
        self,
        metric: str,
        *,
        inputs: JsonObject | None = None,
        parameters: JsonObject | None = None,
        interventions: Sequence[Intervention] | None = None,
        **kwargs: Unpack[RunRequestOptions],
    ) -> SuccessfulJobResult:
        if kwargs.keys() - {"options"}:
            message = "Unsupported run options"
            raise TypeError(message)
        options = kwargs.get("options")
        handle = await self.submit(
            metric,
            inputs=inputs,
            parameters=parameters,
            interventions=interventions,
        )
        state = PollingState(
            handle.job_id,
            options.timeout if options else None,
            options.poll_interval if options else 5.0,
        )
        await handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        state.remaining()
        try:
            result = await self._transport.request(
                endpoints.get_job_result(handle.job_id),
            )
        except (DownloadError, ServiceUnavailableError, JobNotReadyError):
            state.remaining()
            raise
        state.remaining()
        return successful_result(result)

    async def run_to_file(
        self,
        metric: str,
        *,
        inputs: JsonObject | None = None,
        parameters: JsonObject | None = None,
        interventions: Sequence[Intervention] | None = None,
        path: str | os.PathLike[str],
        **kwargs: Unpack[RunRequestOptions],
    ) -> None:
        if kwargs.keys() - {"options"}:
            message = "Unsupported run options"
            raise TypeError(message)
        options = kwargs.get("options")
        handle = await self.submit(
            metric,
            inputs=inputs,
            parameters=parameters,
            interventions=interventions,
        )
        state = PollingState(
            handle.job_id,
            options.timeout if options else None,
            options.poll_interval if options else 5.0,
        )
        await handle._wait(state)  # ruff: ignore[private-member-access] - shared client lifecycle
        state.remaining()
        try:
            await self._transport.download(
                endpoints.result_download_path(handle.job_id),
                path,
                operation="download result",
                atomic=False,
            )
        except (DownloadError, ServiceUnavailableError, JobNotReadyError):
            state.remaining()
            raise
        state.remaining()


class _AdminJobsResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def list(
        self,
        *,
        limit: int = 50,
        status: JobLifecycleStatus,
        offset: int = 0,
    ) -> JobListResponse:
        return await self._transport.request(
            endpoints.list_admin_jobs(limit=limit, status=status, offset=offset)
        )

    async def get(self, job_id: str) -> AdminJobDetail:
        return await self._transport.request(endpoints.get_admin_job(job_id))


class _AdminPluginsResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def list(self) -> InstalledPluginListResponse:
        return await self._transport.request(endpoints.list_plugins())


class _AdminCatalogResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def summary(self) -> CatalogSummaryResponse:
        return await self._transport.request(endpoints.get_admin_catalog())


class _AdminWorkersResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def list(self) -> WorkersResponse:
        return await self._transport.request(endpoints.get_admin_workers())

    async def get(self, name: str) -> WorkerDetail:
        return await self._transport.request(endpoints.get_admin_worker(name))


class _AdminQueueResource:
    def __init__(self, transport: AsyncHTTPTransport) -> None:
        self._transport = transport

    async def get(self) -> QueueSummary:
        return await self._transport.request(endpoints.get_admin_queue())


class AsyncLyraClient(_ClientLifetime):
    """Access Lyra's consumer API with asynchronous requests.

    The client groups endpoints into resource namespaces. Use :attr:`catalog` to
    discover metrics, :attr:`jobs` to submit, run, and observe jobs with
    dictionary arguments, and :attr:`results` to retrieve their outputs.

    Use ``async with`` or await ``aclose()``. Finish outstanding operations,
    or cancel and await them, before closing the client.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: HTTPX timeout for connect, read, write, and pool operations;
            None disables HTTP timeouts.
        headers: Additional headers included with every request.
        agent_api_key: Bearer token for agent-protected job endpoints. Public
            catalog, lookup, and health endpoints do not require it.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        catalog: Asynchronous metric and data-type discovery endpoints.
        jobs: Asynchronous job submission, execution, and status endpoints.
        reports: Durable execution-report inspection and downloads.
        results: Asynchronous job result inspection and download endpoints.

    Example:
        >>> async with AsyncLyraClient(
        ...     "lyra.example.com", agent_api_key="..."
        ... ) as client:
        ...     metrics = await client.catalog.metrics()

    """

    def __init__(
        self,
        host: str,
        timeout: float | httpx.Timeout | None = 30.0,
        headers: dict[str, str] | None = None,
        *,
        agent_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize an asynchronous consumer client and its resources."""
        transport = AsyncHTTPTransport(
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
        """Asynchronous job submission, execution, and status endpoints."""
        self.reports = _ReportsResource(transport)
        """Durable execution-report retrieval and atomic JSON downloads."""
        self.results = _ResultsResource(transport)
        """Asynchronous job result inspection and download endpoints."""

    async def result_tables(
        self, job_id: str
    ) -> dict[str, pd.DataFrame | gpd.GeoDataFrame]:
        """Fetch named tables by job ID.

        Tables restore declared dtypes and CRS from saved output descriptions.
        Returned tables use fresh positional indices.
        Failed jobs raise MetricRunError; unavailable or malformed results raise
        the same errors as other result retrieval operations.

        Returns:
            Every output keyed by its declared name, including empty tables.
        """
        return await self.results.tables(job_id)


class AsyncLyraAdminClient(_ClientLifetime):
    """Access Lyra's administrator API with asynchronous requests.

    Administrator credentials are intentionally isolated from
    `AsyncLyraClient`. This client exposes operational state and mutation
    endpoints, plus the public health checks, but does not expose consumer metric
    execution.

    Use ``async with`` or await ``aclose()``. Finish outstanding operations,
    or cancel and await them, before closing the client.

    Args:
        host: API hostname, optionally including a base path, but without a URL
            scheme.
        timeout: HTTPX timeout for connect, read, write, and pool operations;
            None disables HTTP timeouts.
        headers: Additional headers included with every request.
        admin_api_key: Bearer token for administrator endpoints.
        secure: Use HTTPS when true and HTTP when false.

    Attributes:
        health: Asynchronous liveness and readiness endpoints.
        jobs: Asynchronous administrative job listing and diagnostics endpoints.
        plugins: Asynchronous installed plugin inspection endpoints.
        catalog: Asynchronous administrative catalog summary endpoints.
        workers: Asynchronous worker inspection endpoints.
        queue: Asynchronous queue inspection endpoints.

    Example:
        >>> async with AsyncLyraAdminClient(
        ...     "lyra.example.com", admin_api_key="..."
        ... ) as admin:
        ...     status = await admin.status()

    """

    def __init__(
        self,
        host: str,
        timeout: float | httpx.Timeout | None = 30.0,
        headers: dict[str, str] | None = None,
        *,
        admin_api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize an asynchronous administrator client and its resources."""
        transport = AsyncHTTPTransport(
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
        self.queue = _AdminQueueResource(transport)
        """Asynchronous queue inspection endpoints."""

    async def status(self) -> AdminStatusResponse:
        """Fetch a summary of the running Lyra service.

        Returns:
            API, storage, catalog, queue, and worker configuration status.

        """
        return await self._transport.request(endpoints.get_admin_status())

    async def config_summary(self) -> ConfigSummaryResponse:
        """Fetch the effective non-secret service configuration.

        Returns:
            API host and port, queue name, worker settings, and job retention period.

        """
        return await self._transport.request(endpoints.get_admin_config_summary())
