"""Preflight a native development session and discard its waiting jobs."""

import sys
from pathlib import Path

from redis.exceptions import RedisError
from rq import Queue
from rq.serializers import JSONSerializer

from lyra.runtime.resources import resolve_secret
from lyra.server.config import (
    ensure_runtime_directories,
    load_api_credentials,
    load_config,
)
from lyra.server.db.redis import create_sync_client
from lyra.server.registry import Catalog


def prepare(path: Path) -> None:
    """Validate the development configuration before emptying its queue.

    Raises:
        RuntimeError: If the configured Redis queue cannot be reached or cleared.
    """
    config = load_config(path)
    load_api_credentials()
    Catalog.load(config.plugins)
    for resource in config.resources.values():
        for reference in resource.secrets.values():
            resolve_secret(reference)
    ensure_runtime_directories(config)
    try:
        with create_sync_client(config.redis) as connection:
            connection.ping()
            Queue(
                config.jobs.queue, connection=connection, serializer=JSONSerializer
            ).empty()
    except (RedisError, ValueError, OSError):
        msg = (
            "Cannot access the development Redis queue. "
            "Check --redis-url and the running Redis server."
        )
        raise RuntimeError(msg) from None


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
