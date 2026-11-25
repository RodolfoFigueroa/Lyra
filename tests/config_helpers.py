from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.contracts.config import LyraConfig
from lyra.server.config import ApiCredentials, load_config
from tests.config_serialization import save_config
from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path


def api_credentials() -> ApiCredentials:
    return ApiCredentials(
        admin_api_key="admin-secret",
        agent_api_key="agent-secret",
    )


def load_test_config(
    base: Path,
    *,
    plugins: list[Path] | None = None,
) -> LyraConfig:
    raw_config = {
        "schema_version": 8,
        "result_store_path": base / "results.sqlite3",
        "api": {"public_base_url": "http://127.0.0.1:5219"},
        "valkey": {"url": "redis://valkey:6379/0"},
        "logging": {},
        "jobs": {},
        "plugins": {"installed": [plugin_config(path) for path in plugins or []]},
        "worker": {"temp_dir": base / "cache" / "jobs"},
    }
    config = LyraConfig.model_validate(raw_config)
    config_path = base / "config" / "lyra.toml"
    save_config(config, config_path)
    return load_config(config_path)
