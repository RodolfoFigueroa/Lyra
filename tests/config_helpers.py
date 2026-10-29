from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.sdk.config import InstalledPluginConfig

from lyra_app.config import (
    ApiCredentials,
    DatabaseConfig,
    LyraConfig,
    WorkerCredentials,
    load_config,
)
from tests.config_serialization import save_config
from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path


def api_credentials() -> ApiCredentials:
    return ApiCredentials(
        postgres_password="postgres-" + "secret",
        admin_api_key="admin-secret",
        agent_api_key="agent-secret",
    )


def worker_credentials() -> WorkerCredentials:
    return WorkerCredentials(postgres_password="postgres-" + "secret")


DEFAULT_TEST_PLUGIN = "test-plugin"


def load_test_config(
    base: Path,
    *,
    metric_queues: dict[str, str] | None = None,
    plugins: list[Path] | None = None,
) -> LyraConfig:
    assigned_queues = set((metric_queues or {}).values())
    allowed_queues = sorted(
        {"batch", "heavy", "interactive", "lightweight", "priority-lane"}
        | assigned_queues
    )
    raw_config = {
        "schema_version": 5,
        "api": {"public_base_url": "http://127.0.0.1:5219"},
        "redis": {"url": "redis://redis:6379/0"},
        "database": {"host": "postgres", "port": 5432, "name": "lyra", "user": "lyra"},
        "logging": {},
        "jobs": {},
        "plugins": {
            "default_queue": "interactive",
            "allowed_queues": allowed_queues,
        },
        "workers": {
            "batch": {"queues": ["batch"]},
            "heavy": {"queues": ["heavy"]},
            "interactive": {"queues": ["interactive"]},
            "lightweight": {"queues": ["lightweight"]},
            "priority": {"queues": ["priority-lane"]},
        },
    }
    records = [plugin_config(path, routing=metric_queues) for path in (plugins or [])]
    if metric_queues and not records:
        records = [
            InstalledPluginConfig(
                distribution=DEFAULT_TEST_PLUGIN, routing=metric_queues
            )
        ]
    config = LyraConfig.model_validate(raw_config)
    assert config.database is not None
    config.plugins.installed = records
    config_path = base / "config" / "lyra.toml"
    save_config(config, config_path)
    return load_config(config_path)


def database_config(base: Path) -> DatabaseConfig:
    config = load_test_config(base)
    assert config.database is not None
    return config.database
