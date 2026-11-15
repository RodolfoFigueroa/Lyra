"""Isolated managed adapter fixtures for execution tests."""

from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import ibis
import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ibis.backends.duckdb import Backend as DuckDBBackend
from ibis.backends.postgres import Backend as PostgresBackend

from lyra.sdk import (
    ParquetLocationProvider,
    PostGISLocationProvider,
    PostgresIO,
    Resource,
    ResourceConfig,
    SecretReference,
)
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from tests.provider_schema_helpers import declaration, frame


class ParquetTargets(ParquetLocationProvider):
    id = "targets"
    code_revision = "1"
    schema = declaration()


class PostGISTargets(PostGISLocationProvider):
    id = "targets"
    code_revision = "1"
    schema = declaration()
    db_schema = "city"
    table = "targets"
    database: Annotated[PostgresIO, Resource("database")]


def managed_backend(
    adapter: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Mock, dict, dict[str, ResourceConfig]]:
    query = compile_query_schema(ProviderSchema(declaration()).schema)
    if adapter == "postgres":
        native = Mock(spec=PostgresBackend)
        native.con = Mock()
        native.table.return_value = ibis.table(
            query.schema, name="targets", database="city"
        )
        records = frame().assign(count=pd.Series([10, None], dtype="Int64"))
        data = pa.Table.from_pydict(
            {
                name: records[name].to_wkb() if name == "geometry" else records[name]
                for name in records
            },
            schema=query.interchange,
        )
        native.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: (
            data.to_reader()
        )
        monkeypatch.setattr("ibis.backends.postgres.Backend", Mock(return_value=native))
        monkeypatch.setenv("CROSS_PATH_PASSWORD", "fake")
        return (
            native,
            {"targets": PostGISTargets, "database": PostgresIO},
            {
                "database": ResourceConfig(
                    config={"host": "fake", "database": "fake", "user": "fake"},
                    secrets={"password": SecretReference(env="CROSS_PATH_PASSWORD")},
                )
            },
        )
    native = Mock(spec=DuckDBBackend)
    native.con = Mock()
    types = dict(query.schema.items())
    types["geometry"] = dt.binary
    native.read_parquet.return_value = ibis.table(types, name="targets")
    records = frame().assign(count=pd.Series([10, None], dtype="Int64"))
    data = pa.Table.from_pydict(
        {
            name: records[name].to_wkb() if name == "geometry" else records[name]
            for name in records
        },
        schema=query.interchange,
    )
    path = tmp_path / "targets.parquet"
    pq.write_table(data, path)
    native.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: data.to_reader()
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    return (
        native,
        {"targets": ParquetTargets},
        {"targets": ResourceConfig(config={"path": str(path)})},
    )
