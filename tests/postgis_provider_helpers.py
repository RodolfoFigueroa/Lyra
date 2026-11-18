"""Shared PostGIS declarations, configuration and isolated backend fixtures."""

from typing import Annotated
from unittest.mock import MagicMock, Mock

import pytest
from ibis.backends.postgres import Backend

from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.sdk import (
    Origin,
    PluginDefinition,
    PostGISLocationProvider,
    PostgresIO,
    Resource,
    ResourceConfig,
    SecretReference,
)
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from tests.provider_schema_helpers import declaration


class Places(PostGISLocationProvider):
    id = "places"
    code_revision = "1"
    origins = (Origin(id="city", revision="1"),)
    schema = declaration()
    db_schema = "city data"
    table = "places"
    database: Annotated[PostgresIO, Resource("database")]


def database_config() -> dict[str, ResourceConfig]:
    return {
        "database": ResourceConfig(
            config={"host": "db", "database": "city", "user": "reader"},
            secrets={"password": SecretReference(env="TEST_POSTGIS_PASSWORD")},
        )
    }


def setup(
    provider: type[Places] = Places,
) -> tuple[PreparedProviderInput, ResourceScope]:
    resources = {"places_binding": provider, "database": PostgresIO}
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources=resources)}
    )
    scope = ResourceScope(resources, database_config())
    return PreparedProviderInput(
        "places", frozenset({"a", "b"}), registry["places"]
    ), scope


def native_postgres(
    monkeypatch: pytest.MonkeyPatch, provider: type[Places] = Places
) -> tuple[MagicMock, MagicMock]:
    """Keep native compilation/batch conversion and fake only service boundaries."""
    native = Backend()
    connection = MagicMock()
    monkeypatch.setattr(native, "con", connection, raising=False)
    monkeypatch.setattr(native, "reconnect", Mock())
    query = compile_query_schema(ProviderSchema(provider.schema).schema)
    monkeypatch.setattr(native, "get_schema", Mock(return_value=query.schema))
    monkeypatch.setattr("ibis.backends.postgres.Backend", Mock(return_value=native))
    monkeypatch.setenv("TEST_POSTGIS_PASSWORD", "fixture")
    return connection, connection.cursor.return_value.__enter__.return_value
