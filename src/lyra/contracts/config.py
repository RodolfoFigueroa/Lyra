"""Offline configuration schema shared by the server and administrative CLI."""

import ipaddress
import logging
import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lyra.contracts.resources import ResourceConfig
from lyra.contracts.revisions import BuildRevision

LYRA_DATA_DIR = Path("/lyra_data")
DEFAULT_CONFIG_PATH = LYRA_DATA_DIR / "config" / "lyra.toml"
DEFAULT_API_HOST = str(ipaddress.IPv4Address(0))
DEFAULT_API_PORT = 5219
DEFAULT_FORWARDED_ALLOW_IPS = ["127.0.0.1"]
DEFAULT_RESULT_RETENTION_SECONDS = 86400
DEFAULT_PROGRESS_MIN_INTERVAL_MS = 1000
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_CONCURRENCY = 1
DEFAULT_LOG_DIR = LYRA_DATA_DIR / "logs"
DEFAULT_MCP_MOUNT_PATH = "/mcp"

_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_ALLOWED_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


class StrictConfigModel(BaseModel):
    """Reject unknown configuration keys."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


def _strip_required_string(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        msg = "value must be a non-empty string"
        raise ValueError(msg)
    return stripped


def _validate_absolute_path(path: Path | None) -> Path | None:
    if path is not None and not path.is_absolute():
        msg = "path must be absolute"
        raise ValueError(msg)
    return path


def _strip_string_list(value: list[str]) -> list[str]:
    return [_strip_required_string(item) for item in value]


class ApiConfig(StrictConfigModel):
    """Configure the HTTP server listener, public URL, and trusted proxies."""

    host: str = Field(
        default=DEFAULT_API_HOST,
        description="Interface address on which the API server listens.",
    )
    port: int = Field(
        default=DEFAULT_API_PORT,
        ge=1,
        le=65535,
        description="TCP port on which the API server listens.",
    )
    public_base_url: str = Field(
        min_length=1,
        description="Externally reachable base URL used in result handoffs.",
    )
    forwarded_allow_ips: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORWARDED_ALLOW_IPS),
        description="Proxy IP addresses or CIDRs trusted to set forwarded headers.",
    )

    @field_validator("host", "public_base_url")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        """Strip whitespace and reject blank API string settings.

        Returns:
            The normalized, nonblank setting value.
        """
        return _strip_required_string(value)

    @field_validator("forwarded_allow_ips")
    @classmethod
    def normalize_forwarded_allow_ips(cls, value: list[str]) -> list[str]:
        """Strip and validate every trusted proxy address or CIDR.

        Returns:
            A list containing the normalized proxy entries.
        """
        return _strip_string_list(value)

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        """Validate and normalize the externally reachable HTTP base URL.

        Returns:
            The validated base URL without a trailing slash.

        Raises:
            ValueError: If the URL is malformed, contains credentials or URL
                suffixes, uses an internal hostname, or uses insecure HTTP for a
                non-loopback host.
        """
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            msg = "api.public_base_url must be an absolute http:// or https:// URL"
            raise ValueError(msg)
        if parsed.username is not None or parsed.password is not None:
            msg = "api.public_base_url must not contain credentials"
            raise ValueError(msg)
        if parsed.query or parsed.fragment:
            msg = "api.public_base_url must not contain a query or fragment"
            raise ValueError(msg)

        hostname = parsed.hostname
        if hostname is None:
            msg = "api.public_base_url must contain a hostname"
            raise ValueError(msg)
        try:
            _port = parsed.port
        except ValueError as exc:
            msg = "api.public_base_url contains an invalid port"
            raise ValueError(msg) from exc

        is_loopback = hostname.lower() == "localhost"
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            is_loopback = address.is_loopback
        elif "." not in hostname and not is_loopback:
            msg = (
                "api.public_base_url must use a public hostname, not a "
                "single-label internal hostname"
            )
            raise ValueError(msg)

        if parsed.scheme == "http" and not is_loopback:
            msg = (
                "api.public_base_url must use https; http is allowed only for "
                "loopback development"
            )
            raise ValueError(msg)

        return value.rstrip("/")


class RedisConfig(StrictConfigModel):
    """Configure the Redis endpoint used for queues and retained job state."""

    readiness_timeout_seconds: float = Field(
        default=1.0, gt=0, description="Redis readiness probe deadline."
    )

    url: str = Field(
        min_length=1,
        description="Redis URL used by native RQ jobs and results.",
    )

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        """Strip surrounding whitespace from the Redis URL.

        Returns:
            The normalized, nonblank Redis URL.
        """
        return _strip_required_string(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Require a complete Redis URL with a supported scheme.

        Returns:
            The validated Redis URL unchanged.

        Raises:
            ValueError: If the URL lacks a host or does not use ``redis`` or
                ``rediss``.
        """
        parsed = urlparse(value)
        if parsed.scheme not in _ALLOWED_REDIS_SCHEMES or not parsed.netloc:
            msg = "redis.url must be a redis:// or rediss:// URL"
            raise ValueError(msg)
        return value


class McpConfig(StrictConfigModel):
    """Configure whether and where the MCP server is mounted."""

    enabled: bool = Field(
        default=False,
        description="Whether to mount the Streamable HTTP MCP server.",
    )
    mount_path: str = Field(
        default=DEFAULT_MCP_MOUNT_PATH,
        description="Absolute URL path at which the MCP server is mounted.",
    )

    @field_validator("mount_path")
    @classmethod
    def normalize_mount_path(cls, value: str) -> str:
        """Strip and reject a blank MCP mount path.

        Returns:
            The normalized, nonblank mount path.
        """
        return _strip_required_string(value)

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        """Require an absolute mount path without a trailing slash.

        Returns:
            The validated mount path unchanged.

        Raises:
            ValueError: If the path is not absolute or has a trailing slash.
        """
        if not value.startswith("/"):
            msg = "mcp.mount_path must start with /"
            raise ValueError(msg)
        if len(value) > 1 and value.endswith("/"):
            msg = "mcp.mount_path must not end with /"
            raise ValueError(msg)
        return value


class LoggingConfig(StrictConfigModel):
    """Configure application log severity and optional file output."""

    level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description="Application logging level.",
    )
    file: Path | None = Field(
        default=None,
        description="Optional absolute log file; omit to log to standard output.",
    )

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        """Normalize a logging level name to uppercase.

        Returns:
            The stripped, uppercase logging level name.
        """
        value = _strip_required_string(value)
        return value.upper()

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """Require a logging level recognized by the standard library.

        Returns:
            The recognized logging level name unchanged.

        Raises:
            ValueError: If the standard library does not recognize the level.
        """
        if value not in _ALLOWED_LOG_LEVELS:
            levels = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            msg = f"logging.level must be one of: {levels}"
            raise ValueError(msg)
        return value

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: Path | None) -> Path | None:
        """Require the optional log file path to be absolute.

        Returns:
            The absolute log path, or ``None`` when file logging is disabled.
        """
        return _validate_absolute_path(value)


class JobsConfig(StrictConfigModel):
    """Configure native job execution, retention, and optional progress."""

    queue: str = Field(default="lyra", description="Deployment RQ queue name.")

    @field_validator("queue")
    @classmethod
    def normalize_queue(cls, value: str) -> str:
        """Return the normalized, nonblank deployment queue name."""
        return _strip_required_string(value)

    execution_timeout_seconds: int = Field(
        default=18000, gt=0, description="Native execution timeout in seconds."
    )
    result_retention_seconds: int = Field(
        default=86400,
        gt=0,
        description="Retention for successful and failed jobs in seconds.",
    )
    progress_min_interval_ms: int = Field(
        default=1000,
        ge=0,
        strict=True,
        description="Minimum interval between best-effort progress writes.",
    )


def normalize_distribution_name(value: str) -> str:
    """Validate and normalize a Python distribution name.

    Returns:
        The lowercase name with runs of punctuation replaced by a dash.

    Raises:
        ValueError: If the value is not a Python distribution name.
    """
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value):
        msg = "distribution must be a valid Python package name"
        raise ValueError(msg)
    return re.sub(r"[-_.]+", "-", value).lower()


class InstalledPluginConfig(StrictConfigModel):
    """Select an installed plugin."""

    distribution: str = Field(description="Installed Python distribution name.")
    enabled: bool = Field(
        default=True, description="Include this plugin in the catalog."
    )

    @field_validator("distribution")
    @classmethod
    def normalize_distribution(cls, value: str) -> str:
        """Return the normalized Python distribution name."""
        return normalize_distribution_name(value)


class PluginsConfig(StrictConfigModel):
    """Select installed plugins for the catalog."""

    installed: list[InstalledPluginConfig] = Field(
        default_factory=list, description="Explicitly selected installed plugins."
    )

    @model_validator(mode="after")
    def validate_plugins(self) -> Self:
        """Require unique distribution names.

        Returns:
            The validated plugin configuration.

        Raises:
            ValueError: If distributions repeat.
        """
        names = [plugin.distribution for plugin in self.installed]
        if len(names) != len(set(names)):
            msg = "installed plugin distributions must be unique"
            raise ValueError(msg)
        return self


class WorkerConfig(StrictConfigModel):
    """Configure worker concurrency and scratch storage."""

    concurrency: int = Field(
        default=DEFAULT_WORKER_CONCURRENCY,
        gt=0,
        description="RQ worker processes consuming the deployment queue.",
    )
    temp_dir: Path | None = Field(
        default=None,
        description="Optional absolute per-job temporary-file parent directory.",
    )

    @field_validator("temp_dir")
    @classmethod
    def validate_paths(cls, value: Path | None) -> Path | None:
        """Require optional worker directories to be absolute.

        Returns:
            The absolute directory path, or ``None`` when no override is set.
        """
        return _validate_absolute_path(value)


class LyraConfig(StrictConfigModel):
    """Represent the complete validated runtime configuration for Lyra."""

    build_revision: BuildRevision = Field(
        frozen=True,
        description=(
            "Deployment revision for code, declarations, and provider routing."
        ),
    )

    result_store_path: Path = Field(
        default=LYRA_DATA_DIR / "results.sqlite3",
        description="Shared SQLite result file.",
    )

    @field_validator("result_store_path")
    @classmethod
    def validate_result_store_path(cls, value: Path) -> Path:
        """Return the validated absolute shared SQLite result path."""
        _validate_absolute_path(value)
        return value

    resources: dict[str, ResourceConfig] = Field(
        default_factory=dict, description="Deployment-wide named I/O resource settings."
    )

    schema_version: Literal[6] = Field(
        description="Server configuration schema version."
    )
    api: ApiConfig = Field(description="API bind and public URL settings.")
    redis: RedisConfig = Field(description="Redis connection settings.")
    mcp: McpConfig = Field(
        default_factory=McpConfig,
        description="MCP transport settings.",
    )
    logging: LoggingConfig = Field(description="Application logging settings.")
    jobs: JobsConfig = Field(default_factory=JobsConfig, description="Job settings.")
    plugins: PluginsConfig = Field(description="Installed plugin selection.")
    worker: WorkerConfig = Field(
        default_factory=WorkerConfig, description="Worker settings."
    )

    def worker_temp_dir(self) -> Path:
        """Return the parent directory for per-job scratch files."""
        return self.worker.temp_dir or LYRA_DATA_DIR / "cache" / "jobs"
