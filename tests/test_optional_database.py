"""Application startup and resource configuration boundaries."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lyra_app import config as configuration
from lyra_app import main, worker, worker_launcher
from lyra_app.routes.health import readiness
from tests.config_helpers import api_credentials, load_test_config
from tests.runtime_helpers import make_runtime


def test_api_resolves_only_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    read = Mock(side_effect=AssertionError("unexpected credential access"))
    monkeypatch.setattr(configuration, "read_scalar_env_var", read)
    read.side_effect = lambda name, **_: name
    configuration.load_api_credentials()
    assert [call.args[0] for call in read.call_args_list] == [
        "LYRA_ADMIN_API_KEY",
        "LYRA_AGENT_API_KEY",
    ]
    raw = config.model_dump(exclude_none=True)
    raw["database"] = {}
    with pytest.raises(ValidationError):
        configuration.LyraConfig.model_validate(raw)


@pytest.mark.usefixtures("dummy_redis_clients")
def test_api_lifecycle_and_lookup(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    app = main.create_app(config, credentials=api_credentials())
    with TestClient(app) as client:
        assert set(client.get("/ready").json()) == {"status", "api_version", "redis"}
        response = client.get("/lookups/met-zones", params={"name": "test"})
        assert response.status_code == 404


@pytest.mark.parametrize("healthy", [True, False])
def test_readiness(tmp_path: Path, *, healthy: bool) -> None:
    config = load_test_config(tmp_path)
    runtime = make_runtime(config)
    runtime.async_redis.ping = AsyncMock(return_value=healthy)
    response = Response()
    result = asyncio.run(readiness(response, runtime))
    assert result.redis.status == ("ok" if healthy else "unavailable")
    assert response.status_code == (200 if healthy else 503)


def test_worker_context_creates_scratch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "_runner_temp_base", lambda: tmp_path)
    context = worker.build_run_context("test", node="source")
    assert context.temp_dir.is_dir()


def test_worker_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_test_config(tmp_path)
    for name in (
        "ensure_runtime_directories",
        "configure_logging",
        "refresh_runner_registry",
    ):
        monkeypatch.setattr(worker_launcher, name, Mock())
    connection = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=connection)
    )
    pool = Mock()
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool)
    worker_launcher.launch_worker("interactive", config=config)
    pool.return_value.start.assert_called_once()
    connection.close.assert_called_once()
