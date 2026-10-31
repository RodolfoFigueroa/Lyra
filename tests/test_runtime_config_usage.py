import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from lyra_app.config import LyraConfig, load_config
from lyra_app.db import redis as redis_store
from lyra_app.logging_config import configure_logging
from tests.config_helpers import load_test_config
from tests.config_serialization import save_config


def _reload_test_config(config: LyraConfig, config_path: Path) -> None:
    save_config(config, config_path)
    load_config(config_path)


def test_redis_url_uses_loaded_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config_path = tmp_path / "config" / "lyra.toml"
    configured_url = "redis://configured-redis:6380/2"
    config = config.model_copy(
        update={"redis": config.redis.model_copy(update={"url": configured_url})}
    )
    _reload_test_config(config, config_path)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://env-redis:6379/0")

    assert config.redis.url == configured_url


def test_redis_configuration_uses_mock_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    asynchronous, synchronous = Mock(), Mock()
    async_factory = Mock(return_value=asynchronous)
    sync_factory = Mock(return_value=synchronous)
    monkeypatch.setattr(redis_store.aioredis, "from_url", async_factory)
    monkeypatch.setattr(redis_store.redis, "from_url", sync_factory)

    assert redis_store.create_async_client(config.redis) is asynchronous
    assert redis_store.create_sync_client(config.redis) is synchronous
    for factory in (async_factory, sync_factory):
        factory.assert_called_once()
        assert factory.call_args.args == (config.redis.url,)
        assert factory.call_args.kwargs["socket_timeout"] == 5
        assert factory.call_args.kwargs["socket_connect_timeout"] == 5
    assert async_factory.call_args.kwargs["health_check_interval"] == 30
    assert sync_factory.call_args.kwargs["retry"].get_retries() == 0
    asynchronous.assert_not_called()
    synchronous.assert_not_called()


def test_configure_logging_uses_configured_level_and_file(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    log_file = tmp_path / "logs" / "runtime.log"
    config = config.model_copy(
        update={
            "logging": config.logging.model_copy(
                update={"level": "DEBUG", "file": log_file}
            )
        }
    )
    logger = logging.getLogger("lyra_app")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()

    try:
        configured = configure_logging(config)
        handler = configured.handlers[0]
        configured.info("runtime config log")
        handler.flush()

        assert configured.level == logging.DEBUG
        assert isinstance(handler, logging.FileHandler)
        payload = json.loads(log_file.read_text(encoding="utf-8"))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "lyra_app"
        assert payload["message"] == "runtime config log"
        assert payload["timestamp"].endswith("+00:00")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate
