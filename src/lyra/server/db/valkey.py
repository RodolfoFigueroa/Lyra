"""Explicit Valkey client factories with bounded operation timeouts."""

import redis
import redis.asyncio as aioredis
from redis.backoff import NoBackoff
from redis.retry import Retry

from lyra.contracts.config import ValkeyConfig


def create_sync_client(settings: ValkeyConfig) -> redis.Redis:
    """Create the synchronous coordination connection.

    Returns:
        A synchronous Valkey client.
    """
    return redis.from_url(
        settings.url,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry=Retry(NoBackoff(), 0),
    )


def create_async_client(settings: ValkeyConfig) -> aioredis.Redis:
    """Create the asynchronous readiness connection.

    Returns:
        An asynchronous Valkey client.
    """
    return aioredis.from_url(
        settings.url,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
