from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ibis.backends.duckdb import Backend

from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.sdk import Origin, ParquetLocationProvider, PluginDefinition, ResourceConfig
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration, frame


class Places(ParquetLocationProvider):
    id = "places"
    code_revision = "1"
    origins = (Origin(id="inventory", revision="2026"),)
    schema = declaration()
    schema.columns.update(
        {
            "label": column("string", nullable=True),
            "height": column("Float64", nullable=True),
            "active": column("boolean", nullable=True),
            "integer": column("int64"),
            "number": column("float64"),
            "flag": column("bool"),
        }
    )


def records() -> gpd.GeoDataFrame:
    data = frame()
    data["count"] = pd.Series([2**53 + 1, None], dtype="Int64")
    data["label"] = pd.Series(
        ["first", None],
        dtype=ProviderSchema(Places.schema).schema.columns["label"].dtype.type,
    )
    data["height"] = pd.Series([1.25, None], dtype="Float64")
    data["active"] = pd.Series([True, None], dtype="boolean")
    data["integer"] = pd.Series([2**53 + 1, 2], dtype="int64")
    data["number"] = [1.25, 2.5]
    data["flag"] = [True, False]
    return data[list(ProviderSchema(Places.schema).schema.columns)]


def binary_records(data: gpd.GeoDataFrame | None = None) -> pa.Table:
    data = records() if data is None else data
    values = {
        name: data[name].to_wkb() if name == "geometry" else data[name]
        for name in data.columns
    }
    return pa.Table.from_pydict(
        values,
        schema=compile_query_schema(ProviderSchema(Places.schema).schema).interchange,
    )


@pytest.fixture(name="source")
def source_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "places.parquet"
    pq.write_table(binary_records(), path)
    return path


@pytest.fixture(name="backend")
def backend_fixture(monkeypatch: pytest.MonkeyPatch) -> Mock:
    native = Mock(spec=Backend)
    native.con = Mock()
    query = compile_query_schema(ProviderSchema(Places.schema).schema)
    types = dict(query.schema.items())
    types["geometry"] = dt.binary
    native.read_parquet.return_value = ibis.table(types, name="configured_file")
    native.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: (
        binary_records().to_reader(max_chunksize=1)
    )
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    return native


def setup(
    path: Path, alias: str = "source"
) -> tuple[PreparedProviderInput, ResourceScope]:
    resources = {alias: Places}
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources=resources)}
    )
    return (
        PreparedProviderInput("places", frozenset({"a", "b"}), registry["places"]),
        ResourceScope(resources, {alias: ResourceConfig(config={"path": str(path)})}),
    )


def return_records(backend: Mock, data: pa.Table) -> None:
    backend.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: data.to_reader(
        max_chunksize=1
    )


def transport_schema(schema: pa.Schema) -> pa.Schema:
    # Native DuckDB batches mark fields nullable; the producer enforces the
    # declared nullability on their values.
    return pa.schema([field.with_nullable(nullable=True) for field in schema])
