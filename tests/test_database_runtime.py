"""Census plugin pool ownership with isolated acquisition boundaries."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from lyra.sdk import ResourceConfig, SecretReference
from lyra.sdk.resources import ResourceConfigurationError, ResourceScope
from pydantic import ValidationError
from smoke_plugin import resources
from smoke_plugin.resources import CensusDatabaseConfig, CensusDatabaseIO


def scope(monkeypatch: pytest.MonkeyPatch) -> ResourceScope:
    monkeypatch.setenv("CENSUS_TEST_PASSWORD", "private-password")
    return ResourceScope(
        {"census_db": CensusDatabaseIO},
        {
            "census_db": ResourceConfig(
                config={"host": "db", "name": "census", "user": "reader"},
                secrets={"password": SecretReference(env="CENSUS_TEST_PASSWORD")},
            )
        },
    )


@pytest.mark.parametrize("fail", [False, True])
def test_resource_owns_engine_for_one_execution(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool
) -> None:
    engine = Mock()
    factory = Mock(return_value=engine)
    monkeypatch.setattr(resources.sqlalchemy, "create_engine", factory)
    owned = scope(monkeypatch)
    factory.assert_not_called()
    with (
        pytest.raises(RuntimeError, match="query failed") if fail else nullcontext(),
        owned.activate(),
    ):
        first = owned.get("census_db")
        assert owned.get("census_db") is first
        factory.assert_called_once()
        if fail:
            message = "query failed"
            raise RuntimeError(message)
    engine.dispose.assert_called_once()
    fresh = scope(monkeypatch)
    with fresh.activate():
        assert fresh.get("census_db") is not first
    assert factory.call_count == 2
    assert engine.dispose.call_count == 2


def test_engine_factory_applies_bounded_pool_and_postgres_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock()
    monkeypatch.setattr(resources.sqlalchemy, "create_engine", factory)
    owned = scope(monkeypatch)
    with owned.activate():
        owned.get("census_db")
    url = factory.call_args.args[0]
    assert (url.drivername, url.host, url.database, url.username, url.password) == (
        "postgresql+psycopg",
        "db",
        "census",
        "reader",
        "private-password",
    )
    assert factory.call_args.kwargs == {
        "pool_size": 1,
        "max_overflow": 0,
        "pool_timeout": 5,
        "pool_recycle": 900,
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": 5,
            "options": "-c statement_timeout=300000",
        },
    }


def test_cleanup_clears_engine_even_when_disposal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Mock(dispose=Mock(side_effect=RuntimeError("cleanup failure")))
    monkeypatch.setattr(
        resources.sqlalchemy, "create_engine", Mock(return_value=engine)
    )
    resource = CensusDatabaseIO(
        CensusDatabaseConfig(host="db", name="census", user="reader"),
        {"password": "secret"},
    )
    resource.setup()
    with pytest.raises(RuntimeError, match="cleanup failure"):
        resource.close()
    resource.close()
    engine.dispose.assert_called_once()
    with pytest.raises(RuntimeError, match="not initialized"):
        resource.query_dataframe("SELECT 1")


def test_partial_setup_failure_is_safe_to_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources.sqlalchemy,
        "create_engine",
        Mock(side_effect=RuntimeError("setup failure")),
    )
    owned = scope(monkeypatch)
    with (
        pytest.raises(RuntimeError, match="setup failure"),
        owned.activate(),
    ):
        owned.get("census_db")


@pytest.mark.parametrize("invalid", ["settings", "secret"])
def test_invalid_resource_configuration_never_acquires_engine(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    owned = scope(monkeypatch)
    if invalid == "settings":
        owned.configuration["census_db"].config["host"] = " "
    else:
        monkeypatch.delenv("CENSUS_TEST_PASSWORD")
    factory = Mock()
    monkeypatch.setattr(resources.sqlalchemy, "create_engine", factory)
    with pytest.raises(ResourceConfigurationError), owned.activate():
        owned.get("census_db")
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", " "),
        ("name", ""),
        ("user", " "),
        ("port", 70000),
        ("pool_size", 0),
        ("max_overflow", -1),
        ("pool_timeout_seconds", 0),
        ("connect_timeout_seconds", 0),
        ("statement_timeout_ms", 0),
        ("pool_recycle_seconds", 0),
    ],
)
def test_census_settings_reject_invalid_connection_limits(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        CensusDatabaseConfig.model_validate(
            {
                "host": "db",
                "name": "census",
                "user": "reader",
                field: value,
            }
        )
