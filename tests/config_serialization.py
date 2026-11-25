from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lyra.contracts.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_MCP_MOUNT_PATH,
    ApiConfig,
    JobsConfig,
    LoggingConfig,
    LyraConfig,
    McpConfig,
    PluginsConfig,
    ValkeyConfig,
    WorkerConfig,
)


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value))


def _toml_string_array(values: list[str]) -> str:
    if not values:
        return "[]"
    rendered_values = "\n".join(f"  {_toml_string(value)}," for value in values)
    return f"[\n{rendered_values}\n]"


def _append_key(
    lines: list[str],
    key: str,
    value: str | Path | float | list[str],
) -> None:
    if isinstance(value, int | float):
        rendered = str(value)
    elif isinstance(value, list):
        rendered = _toml_string_array(value)
    else:
        rendered = _toml_string(value)
    lines.append(f"{key} = {rendered}")


def _append_api_section(lines: list[str], api: ApiConfig) -> None:
    lines.append("[api]")
    _append_key(lines, "host", api.host)
    _append_key(lines, "port", api.port)
    _append_key(lines, "public_base_url", api.public_base_url)
    _append_key(lines, "forwarded_allow_ips", api.forwarded_allow_ips)
    lines.append("")


def _append_valkey_section(lines: list[str], valkey: ValkeyConfig) -> None:
    lines.append("[valkey]")
    _append_key(lines, "url", valkey.url)
    _append_key(lines, "readiness_timeout_seconds", valkey.readiness_timeout_seconds)
    lines.append("")


def _append_logging_section(lines: list[str], logging_config: LoggingConfig) -> None:
    lines.append("[logging]")
    _append_key(lines, "level", logging_config.level)
    if logging_config.file is not None:
        _append_key(lines, "file", logging_config.file)
    lines.append("")


def _append_mcp_section(lines: list[str], mcp: McpConfig) -> None:
    lines.extend(("[mcp]", f"enabled = {str(mcp.enabled).lower()}"))
    if mcp.mount_path != DEFAULT_MCP_MOUNT_PATH:
        _append_key(lines, "mount_path", mcp.mount_path)
    lines.append("")


def _append_jobs_section(lines: list[str], jobs: JobsConfig) -> None:
    lines.append("[jobs]")
    for key, value in jobs.model_dump().items():
        _append_key(lines, key, value)
    lines.append("")


def _append_plugins_section(lines: list[str], plugins: PluginsConfig) -> None:
    lines.extend(("[plugins]", ""))
    for plugin in plugins.installed:
        lines.extend(
            [
                "[[plugins.installed]]",
                f"distribution = {_toml_string(plugin.distribution)}",
                f"enabled = {str(plugin.enabled).lower()}",
            ]
        )
        lines.append("")


def _append_worker_section(lines: list[str], worker: WorkerConfig) -> None:
    lines.append("[worker]")
    _append_key(lines, "concurrency", worker.concurrency)
    if worker.temp_dir is not None:
        _append_key(lines, "temp_dir", worker.temp_dir)
    lines.append("")


def render_config_toml(config: LyraConfig) -> str:
    """Serialize a validated runtime configuration as canonical TOML.

    Returns:
        A deterministic TOML document ending in a newline.
    """
    lines: list[str] = [
        f"schema_version = {config.schema_version}",
        "",
    ]
    if config.result_store_path is not None:
        _append_key(lines, "result_store_path", config.result_store_path)
        lines.append("")
    _append_api_section(lines, config.api)
    _append_valkey_section(lines, config.valkey)
    _append_mcp_section(lines, config.mcp)
    _append_logging_section(lines, config.logging)
    _append_jobs_section(lines, config.jobs)
    _append_plugins_section(lines, config.plugins)
    _append_worker_section(lines, config.worker)
    return "\n".join(lines).rstrip() + "\n"


def save_config(config: LyraConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Atomically write a validated runtime configuration as TOML."""
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = render_config_toml(config)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(config_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
