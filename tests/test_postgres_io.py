import os
from typing import Annotated, cast
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from ibis.backends.postgres import Backend

from lyra.contracts.errors import PluginDefinitionError, ResourceConfigurationError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.resources import ResourceScope
from lyra.sdk import (
    BaseIO,
    Origin,
    PluginDefinition,
    PostgresIO,
    Resource,
    ResourceConfig,
    SecretReference,
    metric,
)
from lyra.sdk.postgres import PostgresConfig
from lyra.sdk.resources import resource_metadata


class CityDatabase(PostgresIO):
    def block_count(self) -> int:
        return cast(
            "int", self.backend.table("blocks", database="public").count().execute()
        )


class Reader(BaseIO):
    database: Annotated[PostgresIO, Resource("database")]

    def read(self) -> int:
        return cast("int", self.database.backend.table("blocks").count().execute())

    def close(self) -> None:
        # Dependencies remain usable until their owner closes them.
        assert self.read() == 7


TABLE = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Count", metadata={"unit": None})},
    strict=True,
)


@metric(
    name="count_blocks",
    code_revision="1",
    description="Read directly and through a resource",
    origins=(Origin(id="city", revision="1"),),
    outputs={"counts": TABLE},
)
def count_blocks(
    database: Annotated[CityDatabase, Resource("database")],
    reader: Annotated[Reader, Resource("reader")],
) -> pd.DataFrame:
    assert reader.database is database
    assert reader.database.backend is database.backend
    return pd.DataFrame({"value": [database.block_count(), reader.read()]})


@pytest.fixture
def configuration(monkeypatch: pytest.MonkeyPatch) -> dict[str, ResourceConfig]:
    monkeypatch.setenv("TEST_POSTGRES_PASSWORD", "test-secret")
    return {
        "database": ResourceConfig(
            config={
                "host": "db.example",
                "port": "5433",
                "database": "city",
                "user": "lyra",
            },
            secrets={"password": SecretReference(env="TEST_POSTGRES_PASSWORD")},
        )
    }


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> Mock:
    connect = Mock()
    monkeypatch.setattr("psycopg.connect", connect)
    monkeypatch.setattr(Backend, "_post_connect", Mock())
    table = Mock()
    table.count.return_value.execute.return_value = 7
    monkeypatch.setattr(Backend, "table", Mock(return_value=table))
    return connect


def test_shared_native_backend_and_fresh_execution(
    configuration: dict[str, ResourceConfig], driver: Mock
) -> None:
    runner = LocalRunner.from_definitions(
        {
            "city": PluginDefinition(
                metrics=[count_blocks],
                resources={"database": CityDatabase, "reader": Reader},
            )
        },
        resource_config=configuration,
    )
    driver.assert_not_called()
    for _ in range(2):
        result = runner.run("count_blocks", inputs={})
        assert result.tables["counts"].value.tolist() == [7, 7]
    assert driver.call_count == 2
    assert driver.return_value.close.call_count == 2
    driver.assert_called_with(
        host="db.example",
        port=5433,
        dbname="city",
        user="lyra",
        password=os.environ["TEST_POSTGRES_PASSWORD"],
        autocommit=True,
    )


def test_unused_registration_and_discovery_do_not_acquire(
    driver: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolve = Mock(side_effect=AssertionError("secret resolved"))
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", resolve)
    definitions = {
        "city": PluginDefinition(
            metrics=[count_blocks],
            resources={"database": CityDatabase, "reader": Reader},
        )
    }
    compile_live_catalog(definitions)
    metadata = resource_metadata(CityDatabase)
    assert metadata["secrets"] == ["password"]
    assert "password" not in PostgresConfig.model_fields
    with ResourceScope(definitions["city"].resources).activate():
        pass
    resource = PostgresIO(PostgresConfig(host="db", database="city", user="lyra"), {})
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = resource.backend
    resource.close()
    resolve.assert_not_called()
    driver.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", ""),
        ("database", " "),
        ("user", ""),
        ("port", 0),
        ("port", 65536),
        ("password", "inline-secret"),
    ],
)
def test_invalid_configuration_precedes_connection(
    field: str, value: object, configuration: dict[str, ResourceConfig], driver: Mock
) -> None:
    configuration["database"].config[field] = value
    scope = ResourceScope({"database": PostgresIO}, configuration)
    with pytest.raises(ResourceConfigurationError), scope.activate():
        scope.get("database")
    driver.assert_not_called()


@pytest.mark.parametrize("failure", [None, "query", "setup", "connect"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_lifecycle_and_exception_precedence(
    failure: str | None,
    *,
    cleanup_fails: bool,
    configuration: dict[str, ResourceConfig],
    driver: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("primary failure")
    cleanup_error = ValueError("cleanup failure")
    if cleanup_fails:
        driver.return_value.close.side_effect = cleanup_error
    if failure == "setup":
        monkeypatch.setattr(Backend, "_post_connect", Mock(side_effect=original))
    if failure == "connect":
        driver.side_effect = original
    scope = ResourceScope({"database": PostgresIO}, configuration)
    resource = None

    def execute() -> None:
        nonlocal resource
        with scope.activate():
            resource = scope.get("database")
            assert isinstance(resource, PostgresIO)
            assert isinstance(resource.backend, Backend)
            assert scope.get("database") is resource
            driver.return_value.close.assert_not_called()
            if failure == "query":
                monkeypatch.setattr(
                    resource.backend, "table", Mock(side_effect=original)
                )
                resource.backend.table("blocks")

    expected = original if failure else cleanup_error if cleanup_fails else None
    if expected is None:
        execute()
    else:
        with pytest.raises(type(expected)) as caught:
            execute()
        assert caught.value is expected
    assert driver.return_value.close.call_count == (0 if failure == "connect" else 1)
    assert not scope.instances
    if isinstance(resource, PostgresIO):
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = resource.backend
        resource.close()
        assert driver.return_value.close.call_count == 1


def test_caught_partial_setup_is_not_reusable(
    configuration: dict[str, ResourceConfig],
    driver: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Backend, "_post_connect", Mock(side_effect=RuntimeError("setup"))
    )
    scope = ResourceScope({"database": PostgresIO}, configuration)
    with scope.activate():
        with pytest.raises(RuntimeError, match="setup"):
            scope.get("database")
        with pytest.raises(ResourceConfigurationError, match="did not complete"):
            scope.get("database")
        resource = scope.instances["database"]
        assert isinstance(resource, PostgresIO)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = resource.backend
        driver.return_value.close.assert_not_called()
    driver.assert_called_once()
    driver.return_value.close.assert_called_once()


def test_incompatible_bindings_fail_before_use(driver: Mock) -> None:
    with pytest.raises(ResourceConfigurationError, match="incompatible"):
        ResourceScope({"reader": Reader, "database": BaseIO})
    with pytest.raises(PluginDefinitionError, match="incompatible"):
        compile_live_catalog(
            {
                "city": PluginDefinition(
                    metrics=[count_blocks],
                    resources={"reader": Reader, "database": PostgresIO},
                )
            }
        )
    driver.assert_not_called()
