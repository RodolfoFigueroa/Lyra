"""Validate a native development session and prepare its broker."""

import asyncio
import sys
from pathlib import Path

from redis.exceptions import RedisError
from rq import Queue
from rq.serializers import JSONSerializer

from lyra.contracts.config import ValkeyConfig
from lyra.runtime.resources import resolve_secret
from lyra.server.config import (
    ensure_runtime_directories,
    load_api_credentials,
    load_config,
)
from lyra.server.db.valkey import create_async_client, create_sync_client
from lyra.server.registry import Catalog


async def _wait_for_valkey(settings: ValkeyConfig) -> None:
    async with asyncio.timeout(10), create_async_client(settings) as connection:
        while True:
            try:
                if await connection.ping():
                    return
            except (RedisError, OSError):
                pass
            await asyncio.sleep(0.1)


def prepare(path: Path, *, clear_waiting: bool) -> None:
    """Validate the development configuration and optionally clear waiting jobs.

    Raises:
        RuntimeError: If the configured Valkey queue cannot be reached or cleared.
    """
    config = load_config(path)
    load_api_credentials()
    Catalog.load(config.plugins)
    for resource in config.resources.values():
        for reference in resource.secrets.values():
            resolve_secret(reference)
    ensure_runtime_directories(config)
    try:
        asyncio.run(_wait_for_valkey(config.valkey))
    except (RedisError, ValueError, OSError):
        msg = "Valkey did not answer the development readiness probe."
        raise RuntimeError(msg) from None
    if not clear_waiting:
        return
    try:
        with create_sync_client(config.valkey) as connection:
            Queue(
                config.jobs.queue, connection=connection, serializer=JSONSerializer
            ).empty()
    except (RedisError, ValueError, OSError):
        msg = "Cannot clear waiting jobs from the development Valkey queue."
        raise RuntimeError(msg) from None


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), clear_waiting="--clear-waiting" in sys.argv[2:])
