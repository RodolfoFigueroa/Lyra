"""Startup-only configuration and external credential resolution."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lyra.sdk import config as _sdk_config
from lyra.sdk.config import StrictConfigModel
from lyra.sdk.config import load_config as load_document
from pydantic import ConfigDict, Field, field_validator

DatabaseConfig = _sdk_config.DatabaseConfig
LyraConfig = _sdk_config.LyraConfig
JobsConfig = _sdk_config.JobsConfig
ApiConfig = _sdk_config.ApiConfig
DEFAULT_API_HOST = _sdk_config.DEFAULT_API_HOST
DEFAULT_API_PORT = _sdk_config.DEFAULT_API_PORT
DEFAULT_CONFIG_PATH = _sdk_config.DEFAULT_CONFIG_PATH
DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE = (
    _sdk_config.DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
)
DEFAULT_FORWARDED_ALLOW_IPS = _sdk_config.DEFAULT_FORWARDED_ALLOW_IPS
DEFAULT_RESULT_RETENTION_SECONDS = _sdk_config.DEFAULT_RESULT_RETENTION_SECONDS
DEFAULT_LOG_LEVEL = _sdk_config.DEFAULT_LOG_LEVEL
DEFAULT_MCP_MOUNT_PATH = _sdk_config.DEFAULT_MCP_MOUNT_PATH
DatabasePoolConfig = _sdk_config.DatabasePoolConfig
EarthEngineConfig = _sdk_config.EarthEngineConfig
LYRA_DATA_DIR = _sdk_config.LYRA_DATA_DIR
LoggingConfig = _sdk_config.LoggingConfig
McpConfig = _sdk_config.McpConfig
PluginsConfig = _sdk_config.PluginsConfig
RedisConfig = _sdk_config.RedisConfig
WorkerConfig = _sdk_config.WorkerConfig

LYRA_POSTGRES_PASSWORD_ENV = "LYRA_POSTGRES_PASSWORD"  # ruff:ignore[hardcoded-password-string]
LYRA_ADMIN_API_KEY_ENV = "LYRA_ADMIN_API_KEY"
LYRA_AGENT_API_KEY_ENV = "LYRA_AGENT_API_KEY"


class ConfigSecretError(RuntimeError):
    """Raised when a runtime secret cannot be resolved to a usable value."""


class ConfigLoadError(RuntimeError):
    """Raised when the TOML config file cannot be loaded or validated."""


def read_scalar_env_var(env_var: str, *, field_name: str) -> str:
    """Read a required, nonblank string value from an environment variable.

    Returns:
        The environment value with surrounding whitespace removed.

    Raises:
        ConfigSecretError: If the variable is unset or contains only whitespace.
    """
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        msg = f"{field_name} environment variable is not set: {env_var}"
        raise ConfigSecretError(msg)

    value = raw_value.strip()
    if not value:
        msg = f"{field_name} environment variable is empty: {env_var}"
        raise ConfigSecretError(msg)
    return value


class WorkerCredentials(StrictConfigModel):
    """Validated credentials captured for a worker process."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    postgres_password: str | None = Field(default=None, repr=False, exclude=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_secret(cls, value: str | None) -> str | None:
        """Return a stripped, nonblank credential.

        Raises:
            ValueError: If the credential is blank.
        """
        if value is None:
            return None
        value = value.strip()
        if not value:
            message = "Credential must not be blank"
            raise ValueError(message)
        return value

    def require_postgres_password(self) -> str:
        """Return the configured database credential.

        Raises:
            ConfigSecretError: If the database password is missing.
        """
        if self.postgres_password is None:
            message = "PostgreSQL requires LYRA_POSTGRES_PASSWORD."
            raise ConfigSecretError(message)
        return self.postgres_password


class ApiCredentials(WorkerCredentials):
    """Validated credentials captured for an API process."""

    admin_api_key: str = Field(repr=False, exclude=True)
    agent_api_key: str = Field(repr=False, exclude=True)


def load_worker_credentials(config: LyraConfig) -> WorkerCredentials:
    """Resolve the worker's required environment credential.

    Returns:
        Validated worker credentials.
    """
    return WorkerCredentials(
        postgres_password=read_scalar_env_var(
            LYRA_POSTGRES_PASSWORD_ENV, field_name="postgres_password"
        )
        if config.database is not None
        else None
    )


def load_api_credentials(config: LyraConfig) -> ApiCredentials:
    """Resolve the API's required environment credentials.

    Returns:
        Validated API credentials.
    """
    return ApiCredentials(
        postgres_password=read_scalar_env_var(
            LYRA_POSTGRES_PASSWORD_ENV, field_name="postgres_password"
        )
        if config.database is not None
        else None,
        admin_api_key=read_scalar_env_var(
            LYRA_ADMIN_API_KEY_ENV, field_name="admin_api_key"
        ),
        agent_api_key=read_scalar_env_var(
            LYRA_AGENT_API_KEY_ENV, field_name="agent_api_key"
        ),
    )


def parse_config_toml(raw_config: dict[str, object]) -> LyraConfig:
    """Validate a credential-free configuration document.

    Returns:
        Validated configuration.
    """
    return LyraConfig.model_validate(raw_config)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LyraConfig:
    """Load a credential-free configuration document.

    Returns:
        Validated configuration.

    Raises:
        ConfigLoadError: If the document cannot be loaded or validated.
    """
    try:
        return load_document(path)
    except (OSError, ValueError) as exc:
        msg = f"Cannot load configuration {path}: {exc}"
        raise ConfigLoadError(msg) from exc


def ensure_runtime_directories(config: LyraConfig) -> None:
    """Create non-secret runtime directories declared by the server config."""
    paths: set[Path] = set()
    if config.logging.file is not None:
        paths.add(config.logging.file.parent)

    paths.update(config.worker_temp_dir(worker_name) for worker_name in config.workers)

    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
