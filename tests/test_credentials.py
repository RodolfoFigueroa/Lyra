"""Process credentials and optional Earth Engine startup boundaries."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from lyra_app import auth, main, worker_launcher
from lyra_app.config import (
    ApiCredentials,
    ConfigSecretError,
    EarthEngineConfig,
    LyraConfig,
    WorkerCredentials,
    load_api_credentials,
    load_worker_credentials,
)
from tests.config_helpers import api_credentials, load_test_config, worker_credentials

ENV = ("LYRA_POSTGRES_PASSWORD", "LYRA_ADMIN_API_KEY", "LYRA_AGENT_API_KEY")


@pytest.mark.parametrize("missing", ENV)
@pytest.mark.parametrize("value", [None, " \n"])
def test_api_loader_requires_all_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str, value: str | None
) -> None:
    for name in ENV:
        monkeypatch.setenv(name, " fake-value ")
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(ConfigSecretError, match=missing):
        load_api_credentials(load_test_config(tmp_path))


def test_worker_loader_ignores_api_keys_and_captures_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV[0], " password-value \n")
    for name in ENV[1:]:
        monkeypatch.delenv(name, raising=False)
    credentials = load_worker_credentials(load_test_config(tmp_path))
    assert credentials.postgres_password == "password-" + "value"
    monkeypatch.setenv(ENV[0], "changed")
    assert credentials.postgres_password == "password-" + "value"
    monkeypatch.delenv(ENV[0])
    with pytest.raises(ConfigSecretError, match=ENV[0]):
        load_worker_credentials(load_test_config(tmp_path))


@pytest.mark.parametrize("factory", [api_credentials, worker_credentials])
def test_credentials_are_frozen_and_private(
    factory: Callable[[], WorkerCredentials],
) -> None:
    credentials = factory()
    assert credentials.model_dump() == {}
    assert credentials.model_dump_json() == "{}"
    assert "secret" not in repr(credentials)
    field = "postgres_password"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(credentials, field, str(123))


@pytest.mark.parametrize("model", [ApiCredentials, WorkerCredentials])
def test_credentials_reject_blank_and_missing_values(
    model: type[WorkerCredentials],
) -> None:
    if model is ApiCredentials:
        with pytest.raises(ValidationError):
            model.model_validate({})
    else:
        assert model.model_validate({}).postgres_password is None
    with pytest.raises(ValidationError, match="blank"):
        model.model_validate(
            {"postgres_password": "  ", "admin_api_key": "x", "agent_api_key": "y"}
        )


@pytest.mark.parametrize(
    "section",
    [{}, {"project": " "}, {"project": "ok", "service_account_file": "relative"}],
)
def test_invalid_earth_engine_section(tmp_path: Path, section: dict[str, str]) -> None:
    raw = load_test_config(tmp_path).model_dump()
    raw["earth_engine"] = section
    with pytest.raises(ValidationError):
        LyraConfig.model_validate(raw)


def test_config_and_api_never_read_earth_engine_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.earth_engine = EarthEngineConfig(
        project="project", service_account_file=tmp_path / "absent.json"
    )
    read = Mock(side_effect=AssertionError("credential read"))
    monkeypatch.setattr(auth.Credentials, "from_service_account_file", read)
    parsed = LyraConfig.model_validate(config.model_dump())
    assert main.create_app(parsed, credentials=api_credentials()) is not None
    assert "earth_engine" not in LyraConfig.model_json_schema()["required"]
    read.assert_not_called()


@pytest.mark.parametrize(
    "contents",
    [
        None,
        "",
        " ",
        "invalid",
        "{}",
        "[]",
        "null",
        '{"client_email":"test", "token_uri":"test", "private_key":123}',
    ],
)
def test_earth_engine_file_errors_are_contextual(
    tmp_path: Path, contents: str | None
) -> None:
    path = tmp_path / "credential.json"
    if contents is not None:
        path.write_text(contents)
    with pytest.raises(
        ConfigSecretError, match=r"earth_engine\.service_account_file"
    ) as error:
        auth.initialize_earth_engine(
            EarthEngineConfig(project="project", service_account_file=path)
        )
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_worker_earth_engine_order_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool, failure: bool
) -> None:
    config = load_test_config(tmp_path)
    if enabled:
        config.earth_engine = EarthEngineConfig(project="project")
    calls = []
    for name in (
        "ensure_runtime_directories",
        "configure_logging",
        "probe_worker_database",
    ):
        monkeypatch.setattr(worker_launcher, name, Mock())
    connection = Mock()
    monkeypatch.setattr(
        worker_launcher, "create_sync_client", Mock(return_value=connection)
    )
    initialize = Mock(
        side_effect=RuntimeError("initialization failure")
        if failure
        else lambda _: calls.append("earth_engine")
    )
    monkeypatch.setattr(worker_launcher, "initialize_earth_engine", initialize)
    plugins = Mock(side_effect=lambda *_args, **_kwargs: calls.append("plugins"))
    monkeypatch.setattr(worker_launcher, "refresh_runner_registry", plugins)
    pool = Mock()
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool)
    if enabled and failure:
        with pytest.raises(RuntimeError, match="initialization failure"):
            worker_launcher.launch_worker(
                "interactive", config=config, credentials=worker_credentials()
            )
        plugins.assert_not_called()
        pool.assert_not_called()
    else:
        worker_launcher.launch_worker(
            "interactive", config=config, credentials=worker_credentials()
        )
        assert calls == (["earth_engine"] if enabled else []) + ["plugins"]
        pool.return_value.start.assert_called_once()
    assert initialize.call_count == int(enabled)
    connection.close.assert_called_once()


def test_api_credentials_normalize_and_capture_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ENV:
        monkeypatch.setenv(name, "  captured-value\n")
    credentials = load_api_credentials(load_test_config(tmp_path))
    for name in ENV:
        monkeypatch.setenv(name, "changed")
    assert (
        credentials.postgres_password
        == credentials.admin_api_key
        == credentials.agent_api_key
        == "captured-" + "value"
    )


def test_initialization_failure_preserves_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth.Credentials, "from_service_account_file", Mock())
    cause = RuntimeError("service unavailable")
    monkeypatch.setattr(auth.ee, "Initialize", Mock(side_effect=cause))
    with pytest.raises(RuntimeError, match="Cannot initialize Earth Engine") as error:
        auth.initialize_earth_engine(EarthEngineConfig(project="project"))
    assert error.value.__cause__ is cause
