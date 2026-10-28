import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self, cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from lyra_app.db import connection
from lyra_app.routes import met_zone
from tests.config_helpers import api_credentials, load_test_config


def test_application_database_runtime_owns_async_and_spatial_engines(
    tmp_path: Path,
    dummy_database_engines: tuple[Mock, Mock],
) -> None:
    config = load_test_config(tmp_path)
    runtime = connection.ApplicationDatabaseRuntime(
        config, password=api_credentials().postgres_password
    )

    async def exercise() -> int:
        await runtime.start()
        assert runtime.require_async_engine() is dummy_database_engines[0]
        assert runtime.require_spatial_engine() is dummy_database_engines[1]
        result = await runtime.run_spatial(lambda value: value + 1, 2)
        await runtime.close()
        return result

    assert asyncio.run(exercise()) == 3
    dummy_database_engines[0].dispose.assert_awaited_once()
    dummy_database_engines[1].dispose.assert_called_once()
    with pytest.raises(RuntimeError, match="has not been started"):
        runtime.require_async_engine()


def test_engine_factory_applies_bounded_pool_and_postgres_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    captured: dict[str, Any] = {}
    sentinel = cast("Engine", object())

    def create_engine(url: object, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(connection, "create_engine", create_engine)

    result = connection.create_sync_database_engine(
        config.database.spatial, config, password=api_credentials().postgres_password
    )

    assert result is sentinel
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == pytest.approx(2.0)
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=25000",
    }


def test_worker_engine_is_recreated_after_process_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
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
        config, password=api_credentials().postgres_password
    )
    assert (
        connection.get_worker_engine(
            config, password=api_credentials().postgres_password
        )
        is first
    )
    process_id = 101
    second = connection.get_worker_engine(
        config, password=api_credentials().postgres_password
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
        config, password=api_credentials().postgres_password
    )

    assert statements == ["SELECT 1"]
    assert disposed == [True]


def test_worker_database_probe_disposes_engine_when_connection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
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
            config, password=api_credentials().postgres_password
        )

    assert disposed == [True]


def test_met_zone_lookup_returns_retryable_503_for_database_failure(
    tmp_path: Path,
) -> None:
    config = load_test_config(tmp_path)

    class FailedConnectionContext:
        async def __aenter__(self) -> None:
            statement = "connect"
            message = "unavailable"
            raise OperationalError(statement, {}, Exception(message))

        async def __aexit__(self, *_: object) -> None:
            return None

    database = cast(
        "connection.ApplicationDatabaseRuntime",
        SimpleNamespace(
            config=config,
            require_async_engine=lambda: SimpleNamespace(
                connect=FailedConnectionContext
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(met_zone.get_met_zone_code("Guadalajara", database))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "database_unavailable",
        "message": "The spatial database is temporarily unavailable.",
        "retryable": True,
    }
    assert exc_info.value.headers == {"Retry-After": "5"}


@pytest.mark.parametrize("failure", ["executor", "spatial", "async"])
def test_cleanup_attempts_all_database_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    asynchronous = Mock(dispose=AsyncMock())
    spatial = Mock()
    executor = Mock()
    monkeypatch.setattr(
        connection, "create_async_database_engine", Mock(return_value=asynchronous)
    )
    monkeypatch.setattr(
        connection, "create_sync_database_engine", Mock(return_value=spatial)
    )
    monkeypatch.setattr(connection, "ThreadPoolExecutor", Mock(return_value=executor))
    runtime = connection.ApplicationDatabaseRuntime(
        load_test_config(tmp_path), password=api_credentials().postgres_password
    )
    operations = {
        "executor": executor.shutdown,
        "spatial": spatial.dispose,
        "async": asynchronous.dispose,
    }
    operations[failure].side_effect = RuntimeError("cleanup failure")

    async def exercise() -> None:
        await runtime.start()
        with pytest.raises(RuntimeError, match="cleanup failure"):
            await runtime.close()
        await runtime.close()

    asyncio.run(exercise())
    executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    spatial.dispose.assert_called_once()
    asynchronous.dispose.assert_awaited_once()
    assert runtime.async_engine is None
    assert runtime.spatial_engine is None
