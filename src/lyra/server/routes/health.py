"""Health, readiness, and service-status HTTP endpoints."""

import asyncio

from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from lyra.contracts.observability import (
    LivenessResponse,
    ReadinessResponse,
    RedisHealth,
)
from lyra.server.runtime import RuntimeDependency
from lyra.version import APP_VERSION

router = APIRouter(tags=["Health"])


async def redis_health(connection: Redis, timeout_seconds: float) -> RedisHealth:
    """Probe Redis within a bounded readiness deadline.

    Returns:
        ``ok`` when Redis responds truthfully, otherwise ``unavailable``.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            pong = await connection.ping()
    except (TimeoutError, RedisError):
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


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
            "description": "Redis is unavailable.",
        },
    },
)
async def readiness(
    response: Response,
    runtime: RuntimeDependency,
) -> ReadinessResponse:
    """Report Redis readiness.

    Returns:
        Dependency statuses and an HTTP status reflecting aggregate readiness.
    """
    redis = await redis_health(
        runtime.async_redis, runtime.config.redis.readiness_timeout_seconds
    )
    is_ready = redis.status == "ok"
    response.status_code = (
        status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        api_version=APP_VERSION,
        redis=redis,
    )
