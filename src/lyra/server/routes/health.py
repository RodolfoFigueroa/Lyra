"""Health, readiness, and service-status HTTP endpoints."""

import asyncio
from collections.abc import Callable
from functools import partial

from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from lyra.contracts.observability import (
    LivenessResponse,
    ReadinessResponse,
    StorageHealth,
    ValkeyHealth,
)
from lyra.runtime.terminal_history import probe_storage
from lyra.server.runtime import RuntimeDependency
from lyra.version import APP_VERSION

router = APIRouter(tags=["Health"])


async def valkey_health(connection: Redis, timeout_seconds: float) -> ValkeyHealth:
    """Probe Valkey within a bounded readiness deadline.

    Returns:
        ``ok`` when Valkey responds truthfully, otherwise ``unavailable``.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            pong = await connection.ping()
    except (TimeoutError, RedisError):
        return ValkeyHealth(status="unavailable")
    return ValkeyHealth(status="ok" if pong else "unavailable")


async def sqlite_health(
    probe: Callable[[], None], deadline_seconds: float
) -> StorageHealth:
    """Return SQLite readability within the readiness deadline."""
    try:
        async with asyncio.timeout(deadline_seconds):
            await asyncio.to_thread(probe)
    except (TimeoutError, OSError):
        return StorageHealth(status="unavailable")
    return StorageHealth(status="ok")


@router.get("/live")
async def liveness() -> LivenessResponse:
    """Report that the API process is alive.

    Returns:
        A live status and the running API version.
    """
    return LivenessResponse(status="ok", api_version=APP_VERSION)


@router.get(
    "/ready",
    summary="Check service readiness",
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "Durable storage is unavailable.",
        },
    },
)
async def readiness(
    response: Response,
    runtime: RuntimeDependency,
) -> ReadinessResponse:
    """Report SQLite and Valkey availability.

    Returns:
        Dependency statuses and an HTTP status reflecting aggregate readiness.
    """
    timeout = runtime.config.valkey.readiness_timeout_seconds
    valkey, sqlite = await asyncio.gather(
        valkey_health(runtime.async_valkey, timeout),
        sqlite_health(
            partial(probe_storage, runtime.config.result_store_path), timeout
        ),
    )
    is_ready = sqlite.status == "ok"
    response.status_code = (
        status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse(
        status=("ready" if valkey.status == "ok" else "degraded")
        if is_ready
        else "not_ready",
        sqlite=sqlite,
        api_version=APP_VERSION,
        valkey=valkey,
    )
