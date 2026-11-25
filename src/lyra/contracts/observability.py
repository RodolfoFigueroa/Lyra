"""Models for service health, readiness, and runtime observations."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from lyra.contracts.strict import StrictBaseModel

ReadinessStatus = Literal["ok", "unavailable", "unknown"]
ServiceReadinessStatus = Literal["ready", "degraded", "not_ready"]
WorkerObservedStatus = Literal["online", "offline", "unknown"]


class StorageHealth(StrictBaseModel):
    """SQLite terminal history availability."""

    status: ReadinessStatus


class RedisHealth(StrictBaseModel):
    """Redis readiness observation."""

    status: ReadinessStatus = Field(description="Redis readiness state.")


class LivenessResponse(StrictBaseModel):
    """API process liveness response."""

    status: Literal["ok"] = Field(description="API process liveness state.")
    api_version: str = Field(min_length=1, description="Running Lyra API version.")


class ReadinessResponse(StrictBaseModel):
    """Aggregate readiness response for the API and its dependencies."""

    status: ServiceReadinessStatus = Field(description="Overall API readiness state.")
    api_version: str = Field(min_length=1, description="Running Lyra API version.")
    redis: RedisHealth = Field(description="Redis readiness details.")
    sqlite: StorageHealth


class WorkerConfigSummary(StrictBaseModel):
    """Configured worker process settings."""

    concurrency: int = Field(gt=0)
    temp_dir: str


class ConfigSummaryResponse(StrictBaseModel):
    """Effective service and worker configuration summary."""

    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    queue: str = Field(min_length=1)
    worker: WorkerConfigSummary
    rq_retention_seconds: int = Field(gt=0)


class InstalledPluginSummary(StrictBaseModel):
    """Configured installed plugin metadata."""

    distribution: str = Field(min_length=1)
    version: str | None = None
    enabled: bool


class CatalogSummaryResponse(StrictBaseModel):
    """Plugin catalog contents."""

    metric_count: int = Field(ge=0)
    metric_names: list[str]
    catalog_fingerprint: str
    installed_plugins: list[InstalledPluginSummary]


class WorkerSummary(StrictBaseModel):
    """Native RQ worker process and heartbeat observation."""

    name: str
    hostname: str
    last_heartbeat: datetime | None = None
    heartbeat_age_seconds: float | None = None
    stale: bool
    current_job: str | None = None
    state: str


class WorkerDetail(WorkerSummary):
    """Native worker process detail."""

    pid: int | None = None


class WorkersResponse(StrictBaseModel):
    """Native worker processes registered for the deployment queue."""

    workers: list[WorkerDetail]


class QueueSummary(StrictBaseModel):
    """Pending jobs and active workers on the deployment queue."""

    name: str = Field(min_length=1)
    observed_workers: list[str]
    pending_depth: int = Field(ge=0)


class AdminStatusResponse(StrictBaseModel):
    """High-level operational status for the Lyra service."""

    api_version: str = Field(min_length=1)
    redis: RedisHealth
    metric_count: int = Field(ge=0)
    queue: str = Field(min_length=1)
    worker_concurrency: int = Field(gt=0)
    rq_retention_seconds: int = Field(gt=0)
    catalog_fingerprint: str
