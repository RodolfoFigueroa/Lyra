"""Administrative HTTP endpoints for managing a Lyra deployment."""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.utils import as_text

from lyra.contracts.admin import (
    InstalledPluginListResponse,
    InstalledPluginResponse,
)
from lyra.contracts.config import InstalledPluginConfig, LyraConfig
from lyra.contracts.jobs import (
    AdminJobDetail,
    JobId,
    JobLifecycleStatus,
    JobListResponse,
)
from lyra.contracts.observability import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    InstalledPluginSummary,
    QueueSummary,
    RedisHealth,
    WorkerConfigSummary,
    WorkerDetail,
    WorkersResponse,
)
from lyra.runtime.planning.distributions import installed_version
from lyra.server.job_store import InvalidJobResultError
from lyra.server.routes.job_errors import (
    JOB_LOOKUP_RESPONSES,
    InvalidJobResultHTTPError,
)
from lyra.server.runtime import RuntimeDependency
from lyra.version import APP_VERSION

_bearer = HTTPBearer(scheme_name="AdminBearer")
logger = logging.getLogger(__name__)


def require_admin_key(
    runtime: RuntimeDependency,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    """FastAPI dependency that enforces admin API-key authentication.

    Reads the expected key from the runtime configuration environment.

    Args:
        runtime: Dependencies owned by the requesting application.
        credentials (HTTPAuthorizationCredentials): Bearer token extracted by
            FastAPI's `HTTPBearer` scheme.

    Raises:
        HTTPException: With status 403 if the supplied token does not match.

    """
    expected = runtime.credentials.admin_api_key
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=403, detail="Invalid admin API key.")


router = APIRouter(
    prefix="/admin",
    tags=["Administration"],
    dependencies=[Depends(require_admin_key)],
)


def _plugin_response(plugin: InstalledPluginConfig) -> InstalledPluginResponse:
    return InstalledPluginResponse(
        distribution=plugin.distribution,
        version=installed_version(plugin.distribution),
        enabled=plugin.enabled,
    )


def _redis_health(connection: Redis) -> RedisHealth:
    try:
        pong = connection.ping()
    except RedisError:
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


def _worker_config_summary(config: LyraConfig) -> WorkerConfigSummary:
    return WorkerConfigSummary(
        concurrency=config.worker.concurrency, temp_dir=str(config.worker_temp_dir())
    )


def _installed_plugin_summary(plugin: InstalledPluginConfig) -> InstalledPluginSummary:
    return InstalledPluginSummary(
        distribution=plugin.distribution,
        version=installed_version(plugin.distribution),
        enabled=plugin.enabled,
    )


def _observed_workers(queue: Queue) -> list[WorkerDetail]:
    # Worker.all() removes stale registry entries and constructs worker connections.
    # Read hashes directly so observation neither maintains registries nor changes
    # the API connection's timeout to a worker's blocking-dequeue timeout.
    now = datetime.now(UTC)
    workers = []
    for key in Worker.all_keys(queue=queue):
        raw = queue.connection.hgetall(key)
        if not raw:
            continue
        data = {as_text(field): as_text(value) for field, value in raw.items()}
        heartbeat = (
            datetime.fromisoformat(data["last_heartbeat"])
            if data.get("last_heartbeat")
            else None
        )
        workers.append(
            WorkerDetail(
                name=key.removeprefix(Worker.redis_worker_namespace_prefix),
                hostname=data.get("hostname", ""),
                last_heartbeat=heartbeat,
                heartbeat_age_seconds=max(0, (now - heartbeat).total_seconds())
                if heartbeat
                else None,
                stale=heartbeat is None or (now - heartbeat).total_seconds() > 30,
                current_job=data.get("current_job") or None,
                state=data.get("state", "?"),
                pid=int(data["pid"]) if data.get("pid") else None,
            )
        )
    return workers


@router.get("/plugins")
def list_plugins(runtime: RuntimeDependency) -> InstalledPluginListResponse:
    """List configured installed plugins.

    Returns:
        Installed distribution metadata in configuration order.
    """
    config = runtime.config
    return InstalledPluginListResponse(
        plugins=[_plugin_response(plugin) for plugin in config.plugins.installed]
    )


@router.get("/status")
def get_status(runtime: RuntimeDependency) -> AdminStatusResponse:
    """Summarize deployment health and active runtime configuration.

    Returns:
        API, Redis, catalog, queue, worker, and retention status.
    """
    config = runtime.config
    return AdminStatusResponse(
        api_version=APP_VERSION,
        redis=_redis_health(runtime.redis),
        metric_count=len(runtime.catalog.get_loaded_metric_names()),
        queue=config.jobs.queue,
        worker_concurrency=config.worker.concurrency,
        result_retention_seconds=config.jobs.result_retention_seconds,
        catalog_fingerprint=runtime.catalog.operational_fingerprint,
    )


@router.get("/config-summary")
def get_config_summary(runtime: RuntimeDependency) -> ConfigSummaryResponse:
    """Expose a non-secret summary of the validated runtime configuration.

    Returns:
        API host and port, queue name, worker settings, and job retention period.
    """
    config = runtime.config
    return ConfigSummaryResponse(
        api_host=config.api.host,
        api_port=config.api.port,
        queue=config.jobs.queue,
        worker=_worker_config_summary(config),
        result_retention_seconds=config.jobs.result_retention_seconds,
    )


@router.get("/catalog")
def get_catalog(runtime: RuntimeDependency) -> CatalogSummaryResponse:
    """Summarize the loaded catalog and installed plugins.

    Returns:
        Catalog identity, metric names, and distributions.
    """
    config = runtime.config
    metric_names = runtime.catalog.get_loaded_metric_names()
    return CatalogSummaryResponse(
        metric_count=len(metric_names),
        metric_names=metric_names,
        catalog_fingerprint=runtime.catalog.operational_fingerprint,
        installed_plugins=[
            _installed_plugin_summary(plugin) for plugin in config.plugins.installed
        ],
    )


@router.get("/workers")
def list_workers(runtime: RuntimeDependency) -> WorkersResponse:
    """Return native processes registered on the deployment queue."""
    return WorkersResponse(workers=_observed_workers(runtime.jobs.queue))


@router.get("/workers/{worker_name}")
def get_worker(worker_name: str, runtime: RuntimeDependency) -> WorkerDetail:
    """Return a native worker process by ID.

    Raises:
        HTTPException: If the worker is no longer registered.
    """
    for worker in _observed_workers(runtime.jobs.queue):
        if worker.name == worker_name:
            return worker
    raise HTTPException(status_code=404, detail="Worker not found")


@router.get("/queue")
def get_queue(runtime: RuntimeDependency) -> QueueSummary:
    """Return the deployment queue depth and its active worker IDs."""
    queue = runtime.jobs.queue
    return QueueSummary(
        name=queue.name,
        pending_depth=queue.count,
        observed_workers=[
            worker.name for worker in _observed_workers(queue) if not worker.stale
        ],
    )


@router.get("/jobs")
def list_jobs(
    runtime: RuntimeDependency,
    status: JobLifecycleStatus,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobListResponse:
    """Read one native queue/status page without registry cleanup.

    Returns:
        Retained jobs from the requested native page.
    """
    return JobListResponse(
        jobs=runtime.jobs.list_job_statuses(
            status=status,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/jobs/{job_id}", responses=JOB_LOOKUP_RESPONSES)
def get_job(job_id: JobId, runtime: RuntimeDependency) -> AdminJobDetail:
    """Return native job diagnostics to authenticated administrators.

    Raises:
        HTTPException: If the job expired or does not exist.
        InvalidJobResultHTTPError: If required stored job data is invalid.
    """
    try:
        detail = runtime.jobs.get_job_detail(job_id)
    except InvalidJobResultError as exc:
        raise InvalidJobResultHTTPError from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    return detail
