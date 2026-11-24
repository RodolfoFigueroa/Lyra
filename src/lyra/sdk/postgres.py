"""Execution-owned native PostgreSQL access for resources and metrics."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from pydantic import Field

from lyra.contracts.resources import IOConfig
from lyra.sdk.resources import BaseIO

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ibis.backends.postgres import Backend


class PostgresConfig(IOConfig):
    """Connection settings; passwords belong in resource secret references."""

    host: str = Field(min_length=1, pattern=r"\S")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, pattern=r"\S")
    user: str = Field(min_length=1, pattern=r"\S")


class PostgresIO(BaseIO[PostgresConfig]):
    """Own one lazily connected Ibis backend within an execution scope.

    Subclasses may add ordinary domain query methods. Consumers borrow the
    backend; only the resource owner closes it. Concurrent callers must provide
    their own synchronization. Sharing does not establish a run-wide transaction.
    A pooled implementation must clear temporary objects before returning its session.
    Metrics must declare origins for direct and delegated reads.
    """

    config_model = PostgresConfig
    secret_names = ("password",)

    def __init__(self, config: PostgresConfig, secrets: Mapping[str, str]) -> None:
        """Retain settings without loading the driver or connecting."""
        super().__init__(config, secrets)
        self._backend: Backend | None = None
        self._initialized = False

    @property
    def backend(self) -> Backend:
        """The initialized native backend for borrowed use.

        Raises:
            RuntimeError: If setup has not completed or cleanup has begun.
        """
        if self._backend is None or not self._initialized:
            message = "PostgresIO backend is not initialized"
            raise RuntimeError(message)
        return self._backend

    def setup(self) -> None:
        """Connect lazily, retaining ownership even if initialization fails.

        Raises:
            RuntimeError: If this resource already owns a backend.
        """
        backend_type = cast(
            "type[Backend]", import_module("ibis.backends.postgres").Backend
        )

        if self._backend is not None:
            message = "PostgresIO already owns a backend"
            raise RuntimeError(message)
        # Ibis connect() constructs another backend internally. Retain the
        # configured instance first so failures after driver acquisition can be
        # cleaned up by ResourceScope, with its normal exception precedence.
        self._backend = backend_type(
            **self.config.model_dump(), password=self.secrets["password"]
        )
        self._backend.reconnect()
        self._initialized = True

    def close(self) -> None:
        """Attempt disconnection once, including after partial initialization."""
        backend, self._backend = self._backend, None
        self._initialized = False
        # A driver connection failure can occur before Ibis assigns con.
        if backend is not None and hasattr(backend, "con"):
            backend.disconnect()
