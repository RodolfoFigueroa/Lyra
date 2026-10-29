"""Database connection management and transaction primitives."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import URL, Engine, create_engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from lyra_app.config import DatabaseConfig, DatabasePoolConfig


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when an operation requires omitted PostgreSQL configuration."""

    def __init__(self) -> None:
        """Describe the missing deployment service."""
        super().__init__("PostgreSQL is not configured for this deployment.")


class DatabaseUnavailableError(RuntimeError):
    """Raised when database work cannot start within its service deadline."""


def database_url(
    drivername: str = "postgresql+psycopg",
    *,
    config: DatabaseConfig,
    password: str,
) -> URL:
    """Build a password-bearing SQLAlchemy URL from validated runtime config.

    Returns:
        A PostgreSQL URL using the selected SQLAlchemy driver.
    """
    return URL.create(
        drivername,
        username=config.user,
        password=password,
        host=config.host,
        port=config.port,
        database=config.name,
    )


def _engine_options(
    pool: DatabasePoolConfig,
) -> dict[str, bool | int | float | dict[str, int | str]]:
    return {
        "pool_size": pool.pool_size,
        "max_overflow": pool.max_overflow,
        "pool_timeout": pool.pool_timeout_seconds,
        "pool_recycle": pool.pool_recycle_seconds,
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": pool.connect_timeout_seconds,
            "options": f"-c statement_timeout={pool.statement_timeout_ms}",
        },
    }


def create_sync_database_engine(
    pool: DatabasePoolConfig,
    config: DatabaseConfig,
    password: str,
) -> Engine:
    """Create a synchronous SQLAlchemy engine for one configured workload pool.

    Returns:
        A lazily connecting synchronous database engine.
    """
    return create_engine(
        database_url(config=config, password=password), **_engine_options(pool)
    )


def create_async_database_engine(
    pool: DatabasePoolConfig,
    config: DatabaseConfig,
    password: str,
) -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine for one configured workload pool.

    Returns:
        A lazily connecting asynchronous database engine.
    """
    return create_async_engine(
        database_url(config=config, password=password), **_engine_options(pool)
    )


class ApplicationDatabaseRuntime:
    """Own the API process asynchronous database engine."""

    def __init__(self, config: DatabaseConfig, *, password: str) -> None:
        """Initialize stopped API database resources for a validated config."""
        self.config = config
        self._password = password
        self.async_engine: AsyncEngine | None = None

    async def start(self) -> None:
        """Create the API database engine once."""
        if self.async_engine is not None:
            return
        self.async_engine = create_async_database_engine(
            self.config.api,
            self.config,
            self._password,
        )

    async def close(self) -> None:
        """Dispose the application engine and clear its ownership."""
        engine, self.async_engine = self.async_engine, None
        if engine is not None:
            await engine.dispose()

    def require_async_engine(self) -> AsyncEngine:
        """Return the started asynchronous API engine.

        Returns:
            The engine owned by this runtime.

        Raises:
            RuntimeError: If the runtime has not been started.
        """
        if self.async_engine is None:
            msg = "Application database runtime has not been started."
            raise RuntimeError(msg)
        return self.async_engine


_worker_engine: Engine | None = None
_worker_engine_pid: int | None = None


def get_worker_engine(config: DatabaseConfig, *, password: str) -> Engine:
    """Return the process-local worker engine, rebuilding it after a fork.

    Returns:
        The cached or newly created synchronous worker engine.
    """
    global _worker_engine, _worker_engine_pid  # ruff:ignore[global-statement]

    process_id = os.getpid()
    if _worker_engine is not None and _worker_engine_pid != process_id:
        _worker_engine.dispose(close=False)
        _worker_engine = None
    if _worker_engine is None:
        runtime_config = config
        _worker_engine = create_sync_database_engine(
            runtime_config.worker,
            runtime_config,
            password,
        )
        _worker_engine_pid = process_id
    return _worker_engine


def probe_worker_database(config: DatabaseConfig, *, password: str) -> None:
    """Verify worker database connectivity without retaining a pre-fork engine."""
    engine = create_sync_database_engine(config.worker, config, password)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def dispose_worker_engine() -> None:
    """Dispose and clear the process-local worker database engine."""
    global _worker_engine, _worker_engine_pid  # ruff:ignore[global-statement]

    if _worker_engine is not None:
        _worker_engine.dispose()
    _worker_engine = None
    _worker_engine_pid = None


def is_database_unavailable_error(exc: BaseException) -> bool:
    """Classify transient connection, capacity, and statement-timeout failures.

    Returns:
        ``True`` when callers should present a temporary-unavailability response.
    """
    if isinstance(
        exc,
        DatabaseUnavailableError | OperationalError | SQLAlchemyTimeoutError,
    ):
        return True
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated:
            return True
        sqlstate = getattr(exc.orig, "sqlstate", None)
        return sqlstate == "57014"
    return False
