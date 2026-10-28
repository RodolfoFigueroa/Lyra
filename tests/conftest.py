import socket
from collections.abc import Iterator
from importlib import metadata
from unittest.mock import AsyncMock, Mock

import psycopg
import pytest
import redis
import redis.asyncio as aioredis
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from lyra_app import main
from lyra_app.db import connection
from tests.plugin_helpers import VERSIONS


def _forbid_service_access(*_args: object, **_kwargs: object) -> None:
    pytest.fail(
        "Tests must mock external resource access; real connections are forbidden"
    )


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject unmocked service access, including libpq's native socket path."""
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, _forbid_service_access)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, _forbid_service_access)
    monkeypatch.setattr(psycopg.Connection, "connect", _forbid_service_access)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", _forbid_service_access)
    monkeypatch.setattr(connection, "create_engine", _forbid_service_access)
    monkeypatch.setattr(connection, "create_async_engine", _forbid_service_access)
    monkeypatch.setattr(redis, "from_url", _forbid_service_access)
    monkeypatch.setattr(aioredis, "from_url", _forbid_service_access)


@pytest.fixture
def dummy_database_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Mock, Mock]:
    """Supply engine doubles for runtime lifecycle tests without database clients."""
    asynchronous = Mock(spec=AsyncEngine)
    asynchronous.dispose = AsyncMock()
    spatial = Mock(spec=Engine)
    monkeypatch.setattr(
        connection, "create_async_engine", Mock(return_value=asynchronous)
    )
    monkeypatch.setattr(connection, "create_engine", Mock(return_value=spatial))
    return asynchronous, spatial


@pytest.fixture(autouse=True)
def installed_plugin_metadata(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Supply metadata only for explicitly registered fixture distributions."""
    original = metadata.version
    VERSIONS.clear()

    def version(name: str) -> str:
        if name in VERSIONS:
            return VERSIONS[name]
        return original(name)

    monkeypatch.setattr(metadata, "version", version)
    yield
    VERSIONS.clear()


@pytest.fixture
def dummy_redis_clients(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    """Supply independently closeable Redis clients without service access."""
    synchronous = Mock()
    asynchronous = Mock(aclose=AsyncMock(), ping=AsyncMock(return_value=True))
    monkeypatch.setattr(main, "create_sync_client", Mock(return_value=synchronous))
    monkeypatch.setattr(main, "create_async_client", Mock(return_value=asynchronous))
    return synchronous, asynchronous
