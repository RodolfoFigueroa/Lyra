"""Explicit Redis client factories with bounded operation timeouts."""

import redis
import redis.asyncio as aioredis
from redis.backoff import NoBackoff
from redis.retry import Retry

from lyra_app.config import RedisConfig


def create_sync_client(settings: RedisConfig) -> redis.Redis:
    """Create the synchronous coordination connection.

    Returns:
        A synchronous Redis client.
    """
    return redis.from_url(
        settings.url,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry=Retry(NoBackoff(), 0),
    )


def create_async_client(settings: RedisConfig) -> aioredis.Redis:
    """Create the asynchronous readiness connection.

    Returns:
        An asynchronous Redis client.
    """
    return aioredis.from_url(
        settings.url,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
