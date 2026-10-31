"""Process credentials and lazy plugin-secret resolution."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest
from lyra.sdk import ResourceConfig, SecretReference
from pydantic import ValidationError

from lyra_app import main
from lyra_app.config import (
    ApiCredentials,
    ConfigSecretError,
    LyraConfig,
    load_api_credentials,
)
from tests.config_helpers import api_credentials, load_test_config

ENV = ("LYRA_ADMIN_API_KEY", "LYRA_AGENT_API_KEY")


@pytest.mark.parametrize("missing", ENV)
@pytest.mark.parametrize("value", [None, " \n"])
def test_api_loader_requires_all_credentials(
    monkeypatch: pytest.MonkeyPatch, missing: str, value: str | None
) -> None:
    for name in ENV:
        monkeypatch.setenv(name, " fake-value ")
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(ConfigSecretError, match=missing):
        load_api_credentials()


@pytest.mark.parametrize("factory", [api_credentials])
def test_credentials_are_frozen_and_private(
    factory: Callable[[], ApiCredentials],
) -> None:
    credentials = factory()
    assert credentials.model_dump() == {}
    assert credentials.model_dump_json() == "{}"
    assert "secret" not in repr(credentials)
    field = "admin_api_key"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(credentials, field, str(123))


@pytest.mark.parametrize("model", [ApiCredentials])
def test_credentials_reject_blank_and_missing_values(
    model: type[ApiCredentials],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({})
    with pytest.raises(ValidationError, match="blank"):
        model.model_validate({"admin_api_key": "  ", "agent_api_key": "y"})


def test_config_and_api_never_resolve_resource_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.resources["source"] = ResourceConfig(
        secrets={"token": SecretReference(file=tmp_path / "absent.json")}
    )
    read = Mock(side_effect=AssertionError("credential read"))
    monkeypatch.setattr(SecretReference, "resolve", read)
    parsed = LyraConfig.model_validate(config.model_dump())
    assert main.create_app(parsed, credentials=api_credentials()) is not None
    read.assert_not_called()


def test_api_credentials_normalize_and_capture_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ENV:
        monkeypatch.setenv(name, "  captured-value\n")
    credentials = load_api_credentials()
    for name in ENV:
        monkeypatch.setenv(name, "changed")
    assert (
        credentials.admin_api_key == credentials.agent_api_key == "captured-" + "value"
    )
