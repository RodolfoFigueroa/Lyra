import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyproj
import pytest
import shapely
import sqlglot as sg
from ibis.backends.duckdb import Backend
from pandera.engines.geopandas_engine import Geometry
from sqlglot import expressions as sge

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
)
from lyra.contracts.locations import LocationSelection
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.operations import execute_operation, forward_correspondences
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.provider_acquisition import (
    acquire_provider_candidates,
    acquire_provider_input,
    acquire_provider_membership,
)
from lyra.runtime.resources import ResourceScope
from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk import (
    LocationDependency,
    PluginDefinition,
    ResourceConfig,
    location_operation,
)
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from test_support.location_operations import Places as SourcePlaces
from tests.parquet_helpers import (
    Places,
    backend_fixture,
    binary_records,
    records,
    return_records,
    setup,
    source_fixture,
    transport_schema,
)
from tests.provider_schema_helpers import frame

__all__ = ["backend_fixture", "source_fixture"]


@pytest.mark.parametrize("found", [[], ["a"], ["a", "b"]])
def test_membership_native_id_only_batches(
    source: Path, monkeypatch: pytest.MonkeyPatch, found: list[str]
) -> None:
    # Keep native compilation and Arrow wrapping; no embedded connection is opened.
    native = Backend()
    native.con = Mock()
    data = pa.table({"location_id": pa.array(found, type=pa.string())})
    cursor = Mock()
    cursor.fetch_record_batch.side_effect = lambda **_kwargs: data.to_reader(
        max_chunksize=1
    )
    raw_sql = Mock(return_value=cursor)
    query = compile_query_schema(ProviderSchema(Places.schema).schema)
    types = dict(query.schema.items())
    types["geometry"] = dt.binary
    monkeypatch.setattr(native, "reconnect", Mock())
    monkeypatch.setattr(
        native, "read_parquet", Mock(return_value=ibis.table(types, name="base_file"))
    )
    monkeypatch.setattr(native, "_run_pre_execute_hooks", Mock())
    monkeypatch.setattr(native, "raw_sql", raw_sql)
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    selection, scope = setup(source)
    with scope.activate():
        assert acquire_provider_membership(
            selection.provider, ["a", "b", "missing", "a"], scope
        ) == set(found)
        native.con.close.assert_not_called()
    native.con.close.assert_called_once()
    raw_sql.assert_called_once()  # One logical membership query, even across batches.
    sql = raw_sql.call_args.args[0]
    tree = sg.parse_one(sql, dialect="duckdb")
    assert {column.name for column in tree.find_all(sge.Column)} == {"location_id"}
    assert {table.name for table in tree.find_all(sge.Table)} == {"base_file"}
    assert tree.find(sge.Limit) is None
    assert "ST_" not in sql.upper()
    assert "IN ('a', 'b', 'missing')" in sql


@pytest.mark.parametrize("failure", ["query", "batch", "invalid_ids"])
def test_membership_failure_closes_owned_backend(
    source: Path, backend: Mock, failure: str
) -> None:
    primary = RuntimeError("membership query failed")
    if failure == "query":
        backend.to_pyarrow_batches.side_effect = primary
    elif failure == "batch":

        def batches() -> Iterator[pa.RecordBatch]:
            yield pa.record_batch({"location_id": ["a"]})
            raise primary

        backend.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: (
            pa.RecordBatchReader.from_batches(
                pa.schema({"location_id": pa.string()}), batches()
            )
        )
    else:
        return_records(backend, pa.table({"location_id": [1]}))
    selection, scope = setup(source)
    error = (
        LocationProviderContractError
        if failure == "invalid_ids"
        else LocationAcquisitionError
    )
    with scope.activate(), pytest.raises(error) as caught:
        acquire_provider_membership(selection.provider, ["a", "missing"], scope)
    if failure != "invalid_ids":
        assert caught.value.__cause__ is primary
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("geoparquet", [False, True])
def test_exact_ids_filter_before_complete_typed_materialization(
    source: Path, backend: Mock, monkeypatch: pytest.MonkeyPatch, *, geoparquet: bool
) -> None:
    if geoparquet:
        records().to_parquet(source)
        types = dict(backend.read_parquet.return_value.schema().items())
        types["geometry"] = dt.geometry
        backend.read_parquet.return_value = ibis.table(types, name="configured_file")
    selection, scope = setup(source)
    # The normal producer is the only record validator, after WKB decoding.
    validate = Mock(wraps=selection.provider.schema.validate)
    monkeypatch.setattr(selection.provider.schema, "validate", validate)
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    with scope.activate():
        result = acquire_provider_input(selection, scope)
        pd.testing.assert_frame_equal(result.frame, records())
        validate.assert_called_once()
        assert isinstance(validate.call_args.args[0], gpd.GeoDataFrame)
        backend.read_parquet.assert_called_once_with(str(source))
        expression = backend.to_pyarrow_batches.call_args.args[0]
        assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}
        expected = compile_query_schema(selection.provider.schema.schema)
        assert expression.schema().to_pyarrow() == transport_schema(
            expected.interchange
        )
        sql = Backend().compile(expression, limit=None)
        tree = sg.parse_one(sql, dialect="duckdb")
        assert tree.find(sge.Limit) is None
        membership = tree.find(sge.In)
        assert membership is not None
        assert {value.this for value in membership.expressions} == {"a", "b"}
        assert {table.name for table in tree.find_all(sge.Table)} == {"configured_file"}
        assert "ST_ASWKB" in sql
        assert ("ST_GEOMFROMWKB" in sql) is not geoparquet
        backend.disconnect.assert_not_called()
    backend.disconnect.assert_called_once()


def test_all_null_attributes_and_empty_frames(source: Path, backend: Mock) -> None:
    data = records()
    for name in ("count", "label", "height", "active"):
        data[name] = pd.Series([None, None], dtype=data[name].dtype)
    return_records(backend, binary_records(data))
    selection, scope = setup(source)
    with scope.activate():
        empty = acquire_provider_input(selection, scope, needed_ids=[])
        assert not scope.instances
        pd.testing.assert_frame_equal(
            empty.frame, selection.provider.schema.empty_frame()
        )
        backend.read_parquet.assert_not_called()
        acquired = acquire_provider_input(selection, scope)
        pd.testing.assert_frame_equal(acquired.frame, data)
        return_records(backend, binary_records(data.iloc[:0]))
        provider = cast("Places", scope.get("source"))
        pd.testing.assert_frame_equal(provider.resolve(["missing"]), empty.frame)
        expression = backend.to_pyarrow_batches.call_args.args[0]
        assert expression.schema()["geometry"].is_binary()
        with pytest.raises(LocationProviderContractError, match="match requested IDs"):
            acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "ids", [["a"], ["a", "b", "c"], ["a", "a"], ["", "b"], [None, "b"], [1, 2]]
)
def test_bad_membership_and_ids_use_producer_boundary(
    ids: list, source: Path, backend: Mock
) -> None:
    data = binary_records(records().iloc[[0] * len(ids)])
    data = data.set_column(
        data.schema.get_field_index("location_id"), "location_id", pa.array(ids)
    )
    return_records(backend, data)
    selection, scope = setup(source)
    with pytest.raises(LocationProviderContractError), scope.activate():
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "geometry",
    [None, shapely.Point(), shapely.Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])],
)
def test_invalid_geometry_uses_producer_boundary(
    geometry: object, source: Path, backend: Mock
) -> None:
    data = records()
    data["geometry"] = gpd.GeoSeries([geometry, shapely.Point(1, 1)], crs=data.crs)
    return_records(backend, binary_records(data))
    selection, scope = setup(source)
    with pytest.raises(LocationProviderContractError), scope.activate():
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("name", ["integer", "number", "flag"])
def test_nonnullable_attributes_use_producer_boundary(
    name: str, source: Path, backend: Mock
) -> None:
    data = binary_records()
    field = data.schema.field(name)
    data = data.set_column(
        data.schema.get_field_index(name),
        field,
        pa.array([None, None], type=field.type),
    )
    return_records(backend, data)
    selection, scope = setup(source)
    with pytest.raises(LocationProviderContractError), scope.activate():
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        "malformed_wkb",
        "srid",
        "encoding",
        "untyped_empty",
        "columns",
        "execute",
        "batch",
    ],
)
def test_acquisition_errors_propagate_and_close(
    failure: str, source: Path, backend: Mock
) -> None:
    data = binary_records()
    if failure == "columns":
        data = data.drop(["count"])
    elif failure in {"malformed_wkb", "srid", "encoding", "untyped_empty"}:
        values = {
            "malformed_wkb": pa.array([b"broken", b"broken"]),
            "srid": pa.array(
                [
                    shapely.to_wkb(
                        shapely.set_srid(shapely.Point(0, 0), 3857), include_srid=True
                    )
                ]
                * 2
            ),
            "encoding": pa.array(["POINT (0 0)"] * 2),
            "untyped_empty": pa.nulls(0),
        }[failure]
        if failure == "untyped_empty":
            data = data.slice(0, 0)
        data = data.set_column(
            data.schema.get_field_index("geometry"), "geometry", values
        )
    return_records(backend, data)
    if failure == "execute":
        backend.to_pyarrow_batches.side_effect = RuntimeError("query failed")
    elif failure == "batch":

        def batches() -> Iterator[pa.RecordBatch]:
            yield data.to_batches()[0]
            message = "later batch failed"
            raise RuntimeError(message)

        backend.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: (
            pa.RecordBatchReader.from_batches(data.schema, batches())
        )
    selection, scope = setup(source)
    with pytest.raises(LocationAcquisitionError), scope.activate():
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    ("name", "dtype"),
    [
        ("location_id", dt.int64),
        ("geometry", dt.string),
        ("geometry", dt.GeoSpatial(geotype="geography")),
        ("geometry", dt.GeoSpatial(srid=3857)),
        ("count", dt.float64),
        ("active", dt.int64),
        ("label", dt.binary),
        ("height", None),
    ],
)
def test_incompatible_relation_types_fail_before_execution(
    name: str, dtype: dt.DataType | None, source: Path, backend: Mock
) -> None:
    types = dict(backend.read_parquet.return_value.schema().items())
    if dtype is None:
        del types[name]
    else:
        types[name] = dtype
    backend.read_parquet.return_value = ibis.table(types)
    selection, scope = setup(source)
    with pytest.raises(LocationAcquisitionError) as caught, scope.activate():
        acquire_provider_input(selection, scope)
    assert f"Incompatible Parquet column {name!r}" in str(caught.value.__cause__)
    backend.to_pyarrow_batches.assert_not_called()
    backend.disconnect.assert_called_once()


def test_compatible_source_widths_and_extra_columns(
    source: Path, backend: Mock
) -> None:
    types = dict(backend.read_parquet.return_value.schema().items())
    types.update(count=dt.int32, height=dt.float32, unrelated=dt.string)
    backend.read_parquet.return_value = ibis.table(types)
    data = records()
    data["count"] = pd.Series([1, None], dtype="Int64")
    return_records(backend, binary_records(data))
    selection, scope = setup(source)
    with scope.activate():
        result = acquire_provider_input(selection, scope)
        pd.testing.assert_frame_equal(result.frame, data)
    expression = backend.to_pyarrow_batches.call_args.args[0]
    assert expression.schema().to_pyarrow() == transport_schema(
        compile_query_schema(selection.provider.schema.schema).interchange
    )


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_native_arrow_batch_boundary(
    source: Path, monkeypatch: pytest.MonkeyPatch, *, empty: bool, selected: bool
) -> None:
    # Exercise native Ibis compilation and Arrow wrapping with a fake cursor.
    # In particular, read_all must accept DuckDB's nullable transport fields.
    native = Backend()
    data = binary_records(records().iloc[:0] if empty else records())
    data = data.cast(transport_schema(data.schema))
    cursor = Mock()
    cursor.fetch_record_batch.side_effect = lambda **_kwargs: data.to_reader(
        max_chunksize=1
    )
    raw_sql = Mock(return_value=cursor)
    query = compile_query_schema(ProviderSchema(Places.schema).schema)
    monkeypatch.setattr(native, "reconnect", Mock())
    monkeypatch.setattr(
        native, "read_parquet", Mock(return_value=ibis.table(query.schema))
    )
    monkeypatch.setattr(native, "_run_pre_execute_hooks", Mock())
    monkeypatch.setattr(native, "raw_sql", raw_sql)
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    selection, scope = setup(source)
    with scope.activate():
        provider = cast("Places", scope.get("source"))
        actual = (
            provider.select(query.target_relation(), selection.provider.schema)
            if selected
            else provider.resolve(["a", "b"])
        )
        expected = selection.provider.schema.empty_frame() if empty else records()
        pd.testing.assert_frame_equal(actual, expected)
    assert "LIMIT" not in raw_sql.call_args.args[0]


@pytest.mark.parametrize("metadata_crs", ["declared", "default", "equivalent"])
def test_geoparquet_crs_metadata(
    source: Path, backend: Mock, metadata_crs: str
) -> None:
    records().to_parquet(source)
    data = pq.read_table(source)
    metadata = dict(data.schema.metadata or {})
    geo = json.loads(metadata[b"geo"])
    geometry = geo["columns"]["geometry"]
    if metadata_crs == "default":
        del geometry["crs"]
    elif metadata_crs == "equivalent":
        geometry["crs"] = pyproj.CRS("OGC:CRS84").to_json_dict()
    metadata[b"geo"] = json.dumps(geo).encode()
    pq.write_table(data.replace_schema_metadata(metadata), source)
    selection, scope = setup(source)
    with scope.activate():
        pd.testing.assert_frame_equal(
            acquire_provider_input(selection, scope).frame, records()
        )
        backend.read_parquet.assert_called_once_with(str(source))


@pytest.mark.parametrize(
    "failure",
    [
        "crs",
        "unknown_crs",
        "encoding",
        "type",
        "edges",
        "missing_geometry",
        "malformed",
        "physical_type",
    ],
)
def test_invalid_file_metadata_precedes_backend_reads(
    failure: str, source: Path, backend: Mock
) -> None:
    records().to_parquet(source)
    data = pq.read_table(source)
    metadata = dict(data.schema.metadata or {})
    geo = json.loads(metadata[b"geo"])
    geometry = geo["columns"]["geometry"]
    if failure == "crs":
        geometry["crs"] = pyproj.CRS(3857).to_json_dict()
    elif failure == "unknown_crs":
        geometry["crs"] = None
    elif failure == "encoding":
        geometry["encoding"] = "WKT"
    elif failure == "type":
        geometry["geometry_types"] = ["CircularString"]
    elif failure == "edges":
        geometry["edges"] = "spherical"
    elif failure == "missing_geometry":
        del geo["columns"]["geometry"]
    elif failure == "physical_type":
        data = data.set_column(
            data.schema.get_field_index("geometry"),
            "geometry",
            pa.array(["POINT (0 0)"] * 2),
        )
    metadata[b"geo"] = (
        b"not json" if failure == "malformed" else json.dumps(geo).encode()
    )
    pq.write_table(data.replace_schema_metadata(metadata), source)
    selection, scope = setup(source)
    with pytest.raises(LocationAcquisitionError), scope.activate():
        acquire_provider_input(selection, scope)
    backend.reconnect.assert_not_called()
    backend.read_parquet.assert_not_called()
    backend.to_pyarrow_batches.assert_not_called()


def test_scope_is_stable_across_batches_and_resource_aliases(
    source: Path, backend: Mock
) -> None:
    selection, scope = setup(source)
    alias, _ = setup(source, "another_binding")
    assert selection.provider.scope == alias.provider.scope
    with scope.activate():
        for index, identifier in enumerate(["a", "b"]):
            return_records(backend, binary_records(records().iloc[[index]]))
            acquired = acquire_provider_input(selection, scope, needed_ids=[identifier])
            assert acquired.scope == selection.provider.scope
            assert acquired.frame.location_id.tolist() == [identifier]
        backend.read_parquet.assert_called_once_with(str(source))
        backend.reconnect.assert_called_once()
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("geoparquet", [False, True])
@pytest.mark.parametrize("kind", ["all", "chained", "nulls", "spatial"])
def test_selection_preserves_predicates_and_records(
    source: Path,
    backend: Mock,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    *,
    geoparquet: bool,
) -> None:
    if geoparquet:
        records().to_parquet(source)
        types = dict(backend.read_parquet.return_value.schema().items())
        types["geometry"] = dt.geometry
        backend.read_parquet.return_value = ibis.table(types, name="configured_file")
    selection, scope = setup(source)
    query = selection.provider.query
    assert query is not None
    target = query.target_relation()
    literal = ibis.literal(shapely.Point(1, 2), type=dt.Point(srid=4326))
    if kind == "chained":
        intermediate = target.filter(target.height > 5)
        expression = intermediate.filter(
            intermediate.location_id.isin(["a", "b"]), intermediate.height <= 10
        )
        expected = ['"height" > 5', '"height" <= 10', "IN ('a', 'b')"]
    elif kind == "nulls":
        expression = target.filter(
            target.active | ops.IsNull(target.height.op()).to_expr(),
            ops.NotNull(target.height.op()).to_expr(),
            ~target.flag,
        )
        expected = ['"active" OR', '"height" IS NULL', '"height" IS NOT NULL', "NOT"]
    elif kind == "spatial":
        expression = target.filter(target.height > 5).filter(
            target.geometry.intersects(literal),
            target.geometry.d_within(literal, distance=100),
        )
        expected = [
            '"height" > 5',
            "ST_INTERSECTS",
            "ST_DWITHIN",
            "ST_GEOMFROMTEXT('POINT (1 2)')",
            "100.0",
        ]
    else:
        expression, expected = target, []
    validate_selector_expression(expression, target)
    compiler = Backend()
    original = compiler.compile(expression, limit=None)
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    with scope.activate():
        acquired = acquire_provider_candidates(selection.provider, expression, scope)
        pd.testing.assert_frame_equal(acquired.frame, records())
        assert acquired.scope == selection.provider.scope
        bound = backend.to_pyarrow_batches.call_args.args[0]
        sql = compiler.compile(bound, limit=None)
        tree = sg.parse_one(sql, dialect="duckdb")
        assert {table.name for table in tree.find_all(sge.Table)} == {"configured_file"}
        assert tree.find(sge.Limit) is None
        assert (tree.find(sge.Where) is None) == (kind == "all")
        for predicate in expected:
            assert predicate in sql
        assert ("ST_GEOMFROMWKB" in sql) is not geoparquet
        assert bound.schema().to_pyarrow() == transport_schema(query.interchange)
        assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}
        assert compiler.compile(expression, limit=None) == original
        acquire_provider_input(selection, scope)
        backend.read_parquet.assert_called_once_with(str(source))
        backend.reconnect.assert_called_once()
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("size", [0, 2, 10001])
def test_selection_complete_typed_batches(
    size: int, source: Path, backend: Mock
) -> None:
    data = records().iloc[[0] * size].reset_index(drop=True)
    data["location_id"] = pd.Series(
        [f"p{i}" for i in range(size)], dtype=records().location_id.dtype
    )
    for name in ("count", "height", "active", "label"):
        data[name] = pd.Series([None] * size, dtype=data[name].dtype)
    return_records(backend, binary_records(data))
    selection, scope = setup(source)
    assert selection.provider.query is not None
    with scope.activate():
        result = acquire_provider_candidates(
            selection.provider, selection.provider.query.target_relation(), scope
        )
    expected = data if size else selection.provider.schema.empty_frame()
    pd.testing.assert_frame_equal(result.frame, expected)
    assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("metadata", ["dtype", "embedded"])
def test_selection_rejects_srid_before_translation(
    metadata: str,
    source: Path,
    backend: Mock,
) -> None:
    selection, scope = setup(source)
    assert selection.provider.query is not None
    target = selection.provider.query.target_relation()
    point = shapely.set_srid(shapely.Point(1, 2), 3857 if metadata == "embedded" else 0)
    literal = ibis.literal(
        point, type=dt.Point(srid=3857 if metadata == "dtype" else 4326)
    )
    expression = target.filter(target.geometry.intersects(literal))
    # The shared runtime rejects this before acquisition; the adapter must also
    # guard its metadata-dropping translation when called directly.
    with pytest.raises(ValueError, match="SRID"):
        validate_selector_expression(expression, target)
    with scope.activate(), pytest.raises(ValueError, match="SRID"):
        cast("Places", scope.get("source")).select(
            expression, selection.provider.schema
        )
    backend.to_pyarrow_batches.assert_not_called()
    backend.disconnect.assert_called_once()


def projected_places() -> type[Places]:
    schema = deepcopy(ProviderSchema(Places.schema).schema)
    schema.columns["geometry"].dtype = Geometry(crs="EPSG:3857")
    return type("Projected", (Places,), {"schema": schema})


@pytest.mark.parametrize(
    ("x", "height", "matches"),
    [(0, 6, True), (100, 6, True), (101, 6, False), (0, 5, False), (0, None, False)],
    ids=["inside", "boundary", "outside", "height-threshold", "null"],
)
def test_spatial_selection_forwarding(
    source: Path,
    backend: Mock,
    x: int,
    height: int | None,
    *,
    matches: bool,
) -> None:
    # Declared backend-result fixtures paired with real compilation below.
    # No Python evaluator stands in for DuckDB's spatial or null semantics.
    projected = projected_places()
    data = records().iloc[:1].copy()
    data["location_id"] = "tree"
    data["height"] = pd.Series([height], dtype="Float64")
    data.geometry = [shapely.Point(x, 0)]
    data.set_crs(3857, allow_override=True, inplace=True)
    data = data.iloc[: 1 if matches else 0]
    return_records(backend, binary_records(data))

    def selector(sources: gpd.GeoDataFrame, target: ir.Table) -> ir.Table:
        area = ibis.literal(
            sources.to_crs(3857).geometry.union_all(), type=dt.GeoSpatial(srid=3857)
        )
        return target.filter(
            target.height > 5, target.geometry.d_within(area, distance=100)
        )

    @location_operation(
        name="nearby",
        code_revision="1",
        description="Nearby trees",
        inputs={"source": LocationDependency(provider=SourcePlaces.id)},
        target_provider="places",
        target_selector=selector,
    )
    def mapping(
        sources: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        assert sources.crs is not None
        assert sources.crs.to_epsg() == 4326
        assert candidates.crs is not None
        assert candidates.crs.to_epsg() == 3857
        assert sources.to_crs(candidates.crs).geometry.iloc[0] == shapely.Point(0, 0)
        ids = tuple(candidates.location_id)
        candidates.height = 999
        candidates.geometry = [shapely.Point(99, 99)] * len(candidates)
        return dict.fromkeys(sources.location_id, ids)

    definition = PluginDefinition(
        location_operations=[mapping],
        resources={"source": SourcePlaces, "target": projected},
    )
    registry = compile_provider_registry({"test": definition})
    scope = ResourceScope(
        definition.resources, {"target": ResourceConfig(config={"path": str(source)})}
    )
    binding = BoundPlugin(definition)
    source_data = frame()
    source_data.geometry = [shapely.Point(0, 0)] * 2
    native = NativeLocationInput(
        source_data, registry[SourcePlaces.id].contract.reference
    )
    invocation = binding.prepare_resolved_invocation("nearby", {"source": native})
    resolver = LocationResolver(scope, providers=registry)
    with (
        patch.object(
            projected,
            "resolve",
            side_effect=AssertionError("Unexpected exact-ID lookup"),
        ) as exact,
        scope.activate(),
    ):
        for _ in range(2):
            result = execute_operation(binding, invocation, resolver)
            forwarded = forward_correspondences(
                result, registry["places"].schema.metadata
            )
            assert forwarded.frame.location_id.tolist() == (
                ["tree", "tree"] if matches else []
            )
            assert forwarded.frame.crs is not None
            assert forwarded.frame.crs.to_epsg() == 3857
            if matches:
                assert forwarded.frame.height.tolist() == [height, height]
                assert forwarded.frame.geometry.tolist() == [shapely.Point(x, 0)] * 2
                reused = resolver.resolve(
                    {"later": LocationSelection(provider="places", ids=["tree"])}
                )["later"]
                assert reused.frame.geometry.iloc[0] == shapely.Point(x, 0)
        # Retained records cannot establish complete query membership.
        assert backend.to_pyarrow_batches.call_count == 2
        exact.assert_not_called()
        sql = Backend().compile(
            backend.to_pyarrow_batches.call_args.args[0], limit=None
        )
        assert '"height" > 5' in sql
        assert "ST_DWITHIN" in sql
        assert "100.0" in sql
        assert "ST_GEOMFROMTEXT('POINT (0 0)')" in sql
        pd.testing.assert_frame_equal(
            native.frame,
            frame().assign(geometry=gpd.GeoSeries([shapely.Point(0, 0)] * 2, crs=4326)),
        )
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["unsupported", "execute", "batch", "decode", "schema"]
)
def test_selection_failures_propagate_and_cleanup(
    failure: str,
    source: Path,
    backend: Mock,
) -> None:
    selection, scope = setup(source)
    assert selection.provider.query is not None
    target = selection.provider.query.target_relation()
    expression = target
    data = binary_records()
    if failure == "unsupported":
        # GeoAsBinary is structurally row-wise but unsupported by Ibis 12 DuckDB.
        expression = target.filter(target.geometry.as_binary() == b"value")
        validate_selector_expression(expression, target)
        backend.to_pyarrow_batches.side_effect = lambda expression, **kwargs: (
            Backend().compile(expression, **kwargs)
        )
    elif failure == "execute":
        backend.to_pyarrow_batches.side_effect = RuntimeError("query failed")
    elif failure == "batch":

        def batches() -> Iterator[pa.RecordBatch]:
            yield data.to_batches()[0]
            message = "later batch failed"
            raise RuntimeError(message)

        backend.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: (
            pa.RecordBatchReader.from_batches(data.schema, batches())
        )
    else:
        name, values = (
            ("geometry", [b"broken"] * 2)
            if failure == "decode"
            else ("location_id", ["a"] * 2)
        )
        data = data.set_column(
            data.schema.get_field_index(name), name, pa.array(values)
        )
        return_records(backend, data)
    expected = (
        LocationProviderContractError
        if failure == "schema"
        else LocationAcquisitionError
    )
    with pytest.raises(expected) as caught, scope.activate():
        acquire_provider_candidates(selection.provider, expression, scope)
    if failure == "unsupported":
        assert "GeoAsBinary" in str(caught.value.__cause__)
    backend.disconnect.assert_called_once()
