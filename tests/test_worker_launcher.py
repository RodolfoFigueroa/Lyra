from pathlib import Path
from unittest.mock import Mock

import pytest
from rq.serializers import JSONSerializer

from lyra.server import worker_launcher
from tests.config_helpers import load_test_config


def test_launcher_initializes_and_runs_native_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)

    config.worker.concurrency = 5
    pool = Mock()
    pool_factory = Mock(return_value=pool)
    calls = []
    for name in [
        "ensure_runtime_directories",
        "configure_logging",
    ]:
        monkeypatch.setattr(
            worker_launcher,
            name,
            lambda _config, name=name, **_kwargs: calls.append(name),
        )
    refresh = Mock(
        side_effect=lambda *_args, **_kwargs: calls.append("initialize_worker")
    )
    pool.start.side_effect = lambda **_kwargs: calls.append("start_pool")
    monkeypatch.setattr(worker_launcher, "initialize_worker", refresh)
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool_factory)
    redis_client = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=redis_client)
    )
    worker_launcher.launch_worker(config=config)
    assert calls == [
        "ensure_runtime_directories",
        "configure_logging",
        "initialize_worker",
        "start_pool",
    ]
    refresh.assert_called_once_with(config=config)
    assert pool_factory.call_args.args == ([config.jobs.queue],)
    assert pool_factory.call_args.kwargs["num_workers"] == 5
    assert pool_factory.call_args.kwargs["connection"] is redis_client
    assert pool_factory.call_args.kwargs["serializer"] is JSONSerializer
    assert pool_factory.call_args.kwargs["worker_class"] is worker_launcher.JsonWorker
    pool.start.assert_called_once_with(burst=False, logging_level="INFO")
    redis_client.close.assert_called_once()


def test_launcher_command_uses_deployment_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    monkeypatch.setattr(worker_launcher, "load_config", lambda _path: config)
    launch = Mock()
    monkeypatch.setattr(worker_launcher, "launch_worker", launch)
    worker_launcher.main([])
    launch.assert_called_once_with(config=config)


def test_plugin_failure_stops_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.worker.temp_dir = tmp_path / "scratch"
    probe = Mock(side_effect=RuntimeError("plugin unavailable"))
    initialize = Mock()
    monkeypatch.setattr(worker_launcher, "initialize_worker", probe)
    connection = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=connection)
    )
    monkeypatch.setattr(worker_launcher, "WorkerPool", initialize)
    with pytest.raises(RuntimeError, match="plugin unavailable"):
        worker_launcher.launch_worker(config=config)
    initialize.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("failure", ["initialization", "execution"])
def test_launcher_closes_redis_when_pool_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = load_test_config(tmp_path)
    for name in (
        "ensure_runtime_directories",
        "configure_logging",
        "initialize_worker",
    ):
        monkeypatch.setattr(worker_launcher, name, Mock())
    connection = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=connection)
    )
    pool = Mock()
    factory = Mock(return_value=pool)
    operation = factory if failure == "initialization" else pool.start
    operation.side_effect = RuntimeError("pool failure")
    monkeypatch.setattr(worker_launcher, "WorkerPool", factory)
    with pytest.raises(RuntimeError, match="pool failure"):
        worker_launcher.launch_worker(config=config)
    connection.close.assert_called_once()
