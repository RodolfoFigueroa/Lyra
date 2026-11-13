import json
from unittest.mock import Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
from ibis.backends.postgres import Backend
from pandera.engines.geopandas_engine import Geometry

from lyra.contracts.dependencies import LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.origins import Origin
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk.managed_provider import ManagedLocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from test_support.location_schema import column
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration


class ManagedProvider(FakeProvider, ManagedLocationProvider):
    id = "managed"
    schema = declaration()
    code_revision = "1"
    origins = (Origin(id="inventory"), Origin(id="inventory"))


@pytest.mark.parametrize("string_type", [str, "string", "string[pyarrow]"])
def test_declared_types_and_crs(string_type: str | type[str]) -> None:
    schema = declaration()
    schema.columns.update(
        {
            "label": column(string_type, nullable=True),
            "height": column("Float64", nullable=True),
            "active": column("boolean", nullable=True),
            "integer": column("int64"),
            "number": column("float64"),
            "flag": column("bool"),
        }
    )
    query = compile_query_schema(ProviderSchema(schema).schema)
    assert query.schema == ibis.schema(
        {
            "location_id": dt.String(nullable=False),
            "geometry": dt.GeoSpatial(geotype="geometry", srid=4326, nullable=False),
            "count": dt.Int64(),
            "label": dt.String(),
            "height": dt.Float64(),
            "active": dt.Boolean(),
            "integer": dt.Int64(nullable=False),
            "number": dt.Float64(nullable=False),
            "flag": dt.Boolean(nullable=False),
        }
    )
    assert query.crs == "EPSG:4326"
    assert query.interchange.names == list(schema.columns)
    assert query.interchange.field("location_id") == pa.field(
        "location_id", pa.string(), nullable=False
    )


@pytest.mark.parametrize(
    "rows", [[], [{"location_id": "a", "geometry": b"wkb", "count": None}]]
)
def test_interchange_types_do_not_depend_on_values(rows: list[dict]) -> None:
    query = compile_query_schema(ProviderSchema(declaration()).schema)
    batch = pa.Table.from_pylist(rows, schema=query.interchange)
    assert batch.schema == query.interchange
    assert batch.schema.field("geometry").type == pa.binary()
    assert batch.schema.field("count").type == pa.int64()
    assert not any(pa.types.is_null(field.type) for field in batch.schema)


def test_symbolic_targets_are_invocation_specific() -> None:
    query = compile_query_schema(ProviderSchema(declaration()).schema)
    target = query.target_relation()
    other = query.target_relation()
    impostor = ibis.table(target.schema(), name=target.op().name)
    assert isinstance(target.op(), ops.UnboundTable)
    assert target.op() is not impostor.op()
    assert not target.equals(other)
    selected = target.filter(target["count"] > 1)
    assert selected.op().parent is target.op()
    assert selected.schema() == query.schema
    assert '"count" > 1' in Backend().compile(selected, limit=None)


def test_unsupported_query_conversion_does_not_restrict_exact_ids() -> None:
    schema = declaration()
    # A valid local CRS has no backend-compatible EPSG SRID.
    schema.columns["geometry"].dtype = Geometry(
        crs="+proj=aeqd +lat_0=13.123 +lon_0=17.456 +datum=WGS84 +units=m"
    )
    ordinary = type("Ordinary", (FakeProvider,), {"schema": schema})
    managed = type("Managed", (ManagedProvider,), {"schema": schema})
    assert not capture_provider("ordinary", ordinary).query_capable
    with pytest.raises(PluginDefinitionError, match="EPSG SRID"):
        capture_provider("managed", managed)


def test_unsupported_column_has_clear_query_diagnostic() -> None:
    schema = declaration()
    schema.columns["count"].dtype = "object"
    with pytest.raises(ValueError, match=r"Unsupported query dtype.*count"):
        compile_query_schema(schema)


def test_capability_is_class_based_and_capture_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("acquired during inspection"))
    monkeypatch.setattr(ManagedProvider, "__init__", forbidden)
    monkeypatch.setattr(ManagedProvider, "setup", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    captured = capture_provider("binding", ManagedProvider)
    alias = capture_provider("other_binding", ManagedProvider)
    assert captured.scope == alias.scope
    assert captured.scope.code_revision == "1"
    assert len(captured.scope.origins) == 1
    assert captured.query_capable
    assert captured.query is not None
    inspected = captured.declaration
    assert inspected.query_schema is not None
    assert (
        ProviderDeclaration.model_validate_json(inspected.model_dump_json())
        == inspected
    )
    inspected.query_schema.columns.clear()
    inspected.contract.family.structure.output.columns.clear()
    assert len(captured.declaration.contract.family.structure.output.columns) == 3
    assert len(captured.query.metadata.columns) == 3
    ordinary = type(
        "Impostor", (FakeProvider,), {"query_capable": True, "backend": object()}
    )
    assert not capture_provider("ordinary", ordinary).query_capable
    forbidden.assert_not_called()


def test_discovery_carries_query_metadata_without_executable_objects() -> None:
    @metric(
        name="query_metadata",
        code_revision="1",
        description="Inspect a managed source",
        inputs={"source": LocationDependency(provider="managed")},
        outputs={"result": pandera.DataFrameSchema(strict=True)},
    )
    def inspect_source(source: gpd.GeoDataFrame) -> pd.DataFrame:
        return source

    definition = PluginDefinition(
        metrics=[inspect_source], resources={"source_binding": ManagedProvider}
    )
    view = Discovery(compile_live_catalog({"test": definition}))
    detail = view.inspect("query_metadata")
    payload = json.loads(detail.model_dump_json())
    assert payload["providers"]["managed"]["query_capable"] is True
    assert payload["providers"]["managed"]["query_schema"]["crs"] == "EPSG:4326"
    assert detail.providers["managed"] == definition.providers[0]
    assert detail.providers["managed"].query_schema is not None
    detail.providers["managed"].query_schema.columns.clear()
    fresh = view.inspect("query_metadata").providers["managed"].query_schema
    assert fresh is not None
    assert len(fresh.columns) == 3
