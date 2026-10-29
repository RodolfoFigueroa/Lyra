"""Optional PostgreSQL boundaries with isolated resources."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from lyra.sdk import IOConfig
from lyra.sdk.models.job import JobEnvelope
from pydantic import ValidationError

from lyra_app import config as configuration
from lyra_app import main, worker, worker_launcher
from lyra_app.db.connection import DatabaseNotConfiguredError
from lyra_app.routes.health import readiness
from tests.config_helpers import api_credentials, load_test_config
from tests.runtime_helpers import make_runtime


def test_omitted_database_does_not_resolve_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.database = None
    read = Mock(side_effect=AssertionError("unexpected credential access"))
    monkeypatch.setattr(configuration, "read_scalar_env_var", read)
    assert configuration.load_worker_credentials(config).postgres_password is None
    read.assert_not_called()
    read.side_effect = lambda name, **_: name
    credentials = configuration.load_api_credentials(config)
    assert credentials.postgres_password is None
    assert [call.args[0] for call in read.call_args_list] == [
        "LYRA_ADMIN_API_KEY",
        "LYRA_AGENT_API_KEY",
    ]
    raw = config.model_dump(exclude_none=True)
    assert configuration.LyraConfig.model_validate(raw).database is None
    raw["database"] = {}
    with pytest.raises(ValidationError):
        configuration.LyraConfig.model_validate(raw)


def test_direct_startup_requires_configured_password(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    with pytest.raises(configuration.ConfigSecretError):
        main.create_app(
            config,
            credentials=configuration.ApiCredentials(
                admin_api_key="a", agent_api_key="b"
            ),
        )
    with pytest.raises(configuration.ConfigSecretError):
        worker_launcher.launch_worker(
            "interactive", config=config, credentials=configuration.WorkerCredentials()
        )


@pytest.mark.usefixtures("dummy_redis_clients")
def test_database_free_api_lifecycle_and_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.database = None
    factory = Mock(side_effect=AssertionError("database acquired"))
    monkeypatch.setattr(main, "ApplicationDatabaseRuntime", factory)
    app = main.create_app(config, credentials=api_credentials())
    with TestClient(app) as client:
        assert app.state.runtime.database is None
        assert client.get("/ready").json()["database"] == {"status": "not_configured"}
        response = client.get("/lookups/met-zones", params={"name": "test"})
        assert response.status_code == 404
    factory.assert_not_called()


@pytest.mark.parametrize("healthy", [True, False])
def test_database_free_readiness(tmp_path: Path, *, healthy: bool) -> None:
    config = load_test_config(tmp_path)
    config.database = None
    runtime = make_runtime(config)
    runtime.async_redis.ping = AsyncMock(return_value=healthy)
    response = Response()
    result = asyncio.run(readiness(response, runtime))
    assert result.database.status == "not_configured"
    assert response.status_code == (200 if healthy else 503)


def test_worker_context_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.database = None
    monkeypatch.setattr(worker, "require_worker_config", lambda: config)
    monkeypatch.setattr(worker, "_runner_temp_base", lambda: tmp_path)
    context = worker.build_run_context(
        JobEnvelope(job_id="test", metric="root", input={}), node="source"
    )
    assert context.temp_dir.is_dir()
    with pytest.raises(DatabaseNotConfiguredError):
        worker.WorkerDatabaseIO(IOConfig(), {}).setup()


def test_database_free_worker_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.database = None
    for name in (
        "ensure_runtime_directories",
        "configure_logging",
        "refresh_runner_registry",
    ):
        monkeypatch.setattr(worker_launcher, name, Mock())
    probe = Mock(side_effect=AssertionError("unexpected database probe"))
    monkeypatch.setattr(worker_launcher, "probe_worker_database", probe)
    connection = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=connection)
    )
    pool = Mock()
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool)
    worker_launcher.launch_worker(
        "interactive", config=config, credentials=configuration.WorkerCredentials()
    )
    probe.assert_not_called()
    pool.return_value.start.assert_called_once()
    connection.close.assert_called_once()
