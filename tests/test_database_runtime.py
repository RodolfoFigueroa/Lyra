import asyncio
from pathlib import Path
from typing import Any, Self, cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from lyra_app.db import connection
from tests.config_helpers import api_credentials, database_config, load_test_config


def test_application_database_runtime_owns_async_engine(
    tmp_path: Path,
    dummy_database_engines: tuple[Mock, Mock],
) -> None:
    config = load_test_config(tmp_path)
    assert config.database is not None
    runtime = connection.ApplicationDatabaseRuntime(
        config.database, password=api_credentials().require_postgres_password()
    )

    async def exercise() -> None:
        await runtime.start()
        assert runtime.require_async_engine() is dummy_database_engines[0]
        await runtime.close()

    asyncio.run(exercise())
    dummy_database_engines[0].dispose.assert_awaited_once()
    dummy_database_engines[1].dispose.assert_not_called()
    with pytest.raises(RuntimeError, match="has not been started"):
        runtime.require_async_engine()


def test_engine_factory_applies_bounded_pool_and_postgres_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    assert config.database is not None
    captured: dict[str, Any] = {}
    sentinel = cast("Engine", object())

    def create_engine(url: object, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(connection, "create_engine", create_engine)

    result = connection.create_sync_database_engine(
        config.database.api,
        config.database,
        password=api_credentials().require_postgres_password(),
    )

    assert result is sentinel
    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == pytest.approx(2.0)
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=10000",
    }


def test_worker_engine_is_recreated_after_process_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    assert config.database is not None
    connection.dispose_worker_engine()
    process_id = 100
    engines: list[Any] = []

    class FakeEngine:
        def __init__(self) -> None:
            self.dispose_calls: list[bool] = []

        def dispose(self, *, close: bool = True) -> None:
            self.dispose_calls.append(close)

    def create_engine(*_: object) -> Engine:
        engine = FakeEngine()
        engines.append(engine)
        return cast("Engine", engine)

    monkeypatch.setattr(connection, "create_sync_database_engine", create_engine)
    monkeypatch.setattr(connection.os, "getpid", lambda: process_id)

    first = connection.get_worker_engine(
        config.database, password=api_credentials().require_postgres_password()
    )
    assert (
        connection.get_worker_engine(
            config.database, password=api_credentials().require_postgres_password()
        )
        is first
    )
    process_id = 101
    second = connection.get_worker_engine(
        config.database, password=api_credentials().require_postgres_password()
    )

    assert second is not first
    assert engines[0].dispose_calls == [False]
    connection.dispose_worker_engine()
    assert engines[1].dispose_calls == [True]


def test_worker_database_probe_executes_query_and_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    assert config.database is not None
    statements: list[str] = []
    disposed: list[bool] = []

    class FakeConnection:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def execute(statement: object) -> None:
            statements.append(str(statement))

    class FakeEngine:
        @staticmethod
        def connect() -> FakeConnection:
            return FakeConnection()

        @staticmethod
        def dispose() -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, _runtime_config, _password: cast("Engine", FakeEngine()),
    )

    connection.probe_worker_database(
        config.database, password=api_credentials().require_postgres_password()
    )

    assert statements == ["SELECT 1"]
    assert disposed == [True]


def test_worker_database_probe_disposes_engine_when_connection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    assert config.database is not None
    disposed: list[bool] = []

    class FailedEngine:
        @staticmethod
        def connect() -> None:
            statement = "connect"
            message = "unavailable"
            raise OperationalError(statement, {}, Exception(message))

        @staticmethod
        def dispose() -> None:
            disposed.append(True)

    monkeypatch.setattr(
        connection,
        "create_sync_database_engine",
        lambda _pool, _runtime_config, _password: cast("Engine", FailedEngine()),
    )

    with pytest.raises(OperationalError):
        connection.probe_worker_database(
            config.database, password=api_credentials().require_postgres_password()
        )

    assert disposed == [True]


def test_cleanup_clears_engine_even_when_disposal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asynchronous = Mock(dispose=AsyncMock(side_effect=RuntimeError("cleanup failure")))
    monkeypatch.setattr(
        connection, "create_async_database_engine", Mock(return_value=asynchronous)
    )
    runtime = connection.ApplicationDatabaseRuntime(
        database_config(tmp_path),
        password=api_credentials().require_postgres_password(),
    )

    async def exercise() -> None:
        await runtime.start()
        with pytest.raises(RuntimeError, match="cleanup failure"):
            await runtime.close()
        await runtime.close()

    asyncio.run(exercise())
    asynchronous.dispose.assert_awaited_once()
    assert runtime.async_engine is None
