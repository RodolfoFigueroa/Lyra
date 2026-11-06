"""Startup-only configuration and external credential resolution."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import ConfigDict, Field, field_validator

from lyra.contracts.config import DEFAULT_CONFIG_PATH, LyraConfig, StrictConfigModel

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


class ApiCredentials(StrictConfigModel):
    """Validated credentials captured for an API process."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    admin_api_key: str = Field(repr=False, exclude=True)
    agent_api_key: str = Field(repr=False, exclude=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_secret(cls, value: str) -> str:
        """Return a stripped, nonblank credential.

        Raises:
            ValueError: If the credential is blank.
        """
        value = value.strip()
        if not value:
            message = "Credential must not be blank"
            raise ValueError(message)
        return value


def load_api_credentials() -> ApiCredentials:
    """Resolve the API's required environment credentials.

    Returns:
        Validated API credentials.
    """
    return ApiCredentials(
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
        with Path(path).open("rb") as source:
            return LyraConfig.model_validate(tomllib.load(source))
    except (OSError, ValueError) as exc:
        msg = f"Cannot load configuration {path}: {exc}"
        raise ConfigLoadError(msg) from exc


def ensure_runtime_directories(config: LyraConfig) -> None:
    """Create non-secret runtime directories declared by the server config."""
    paths: set[Path] = {config.artifact_dir}
    if config.logging.file is not None:
        paths.add(config.logging.file.parent)

    paths.update(config.worker_temp_dir(worker_name) for worker_name in config.workers)

    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
