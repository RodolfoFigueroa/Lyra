"""Application-owned dependencies and typed request accessors."""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Request
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from lyra_app.config import ApiCredentials, LyraConfig
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.job_store import JobStore
from lyra_app.registry import Catalog


@dataclass
class ApplicationRuntime:
    """Resources owned by a single running application."""

    config: LyraConfig
    catalog: Catalog
    redis: Redis
    async_redis: AsyncRedis
    jobs: JobStore
    database: ApplicationDatabaseRuntime | None
    credentials: ApiCredentials


def require_runtime(app: FastAPI) -> ApplicationRuntime:
    """Return the initialized runtime.

    Raises:
        RuntimeError: If the application lifespan is not active.
    """
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        message = "Application runtime is unavailable."
        raise RuntimeError(message)
    return cast("ApplicationRuntime", runtime)


def get_runtime(request: Request) -> ApplicationRuntime:
    """Resolve dependencies from the requesting application.

    Returns:
        The active application runtime.
    """
    return require_runtime(request.app)


RuntimeDependency = Annotated[ApplicationRuntime, Depends(get_runtime)]


def get_configuration(runtime: RuntimeDependency) -> LyraConfig:
    """Return the captured application configuration."""
    return runtime.config


def get_catalog(runtime: RuntimeDependency) -> Catalog:
    """Return the validated application catalog."""
    return runtime.catalog


def get_jobs(runtime: RuntimeDependency) -> JobStore:
    """Return the application job store."""
    return runtime.jobs


ConfigDependency = Annotated[LyraConfig, Depends(get_configuration)]
CatalogDependency = Annotated[Catalog, Depends(get_catalog)]
JobStoreDependency = Annotated[JobStore, Depends(get_jobs)]
