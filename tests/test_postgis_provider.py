from typing import Annotated, cast
from unittest.mock import MagicMock, Mock, call

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
import shapely
import sqlglot as sg
from ibis.backends.postgres import Backend
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point, Polygon
from sqlglot import expressions as sge

from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    PluginDefinitionError,
)
from lyra.contracts.locations import LocationSelection
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.operations import execute_operation, forward_correspondences
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.provider_acquisition import (
    acquire_provider_candidates,
    acquire_provider_input,
)
from lyra.runtime.resources import ResourceScope
from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk import (
    BaseIO,
    LocationDependency,
    Origin,
    PluginDefinition,
    PostGISLocationProvider,
    PostgresIO,
    Resource,
    ResourceConfig,
    SecretReference,
    location_operation,
    metric,
)
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from test_support.location_operations import Places as SourcePlaces
from tests.provider_schema_helpers import declaration, frame


class Places(PostGISLocationProvider):
    id = "places"
    code_revision = "1"
    origins = (Origin(id="city", revision="1"),)
    schema = declaration()
    db_schema = "city data"
    table = "places"
    database: Annotated[PostgresIO, Resource("database")]


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv("TEST_POSTGIS_PASSWORD", "fixture")
    native = Mock(spec=Backend)
    native.con = Mock()
    native.table.return_value = ibis.table(
        compile_query_schema(ProviderSchema(Places.schema).schema).schema,
        name="places",
        database="city data",
    )
    return_records(native, frame())
    monkeypatch.setattr("ibis.backends.postgres.Backend", Mock(return_value=native))
    return native


def return_records(backend: Mock, data: pd.DataFrame) -> None:
    types = backend.table.return_value.schema()
    columns = {}
    for name in types.names:
        values = data[name]
        dtype = types[name].to_pyarrow() if name != "geometry" else pa.binary()
        if name == "geometry" and isinstance(values, gpd.GeoSeries):
            values = values.map(
                lambda value: (
                    shapely.set_srid(value, data.crs.to_epsg())
                    if value is not None and not shapely.get_srid(value)
                    else value
                )
            ).to_wkb(include_srid=True)
        if name == "location_id":
            columns[name] = pa.array(values)
        else:
            columns[name] = pa.array(values, type=dtype)
    batch = pa.Table.from_pydict(columns)
    backend.to_pyarrow_batches.side_effect = lambda *_args, **_kwargs: batch.to_reader(
        max_chunksize=1
    )


def setup(
    provider: type[Places] = Places,
) -> tuple[PreparedProviderInput, ResourceScope]:
    resources = {"places_binding": provider, "database": PostgresIO}
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources=resources)}
    )
    scope = ResourceScope(
        resources,
        {
            "database": ResourceConfig(
                config={"host": "db", "database": "city", "user": "reader"},
                secrets={"password": SecretReference(env="TEST_POSTGIS_PASSWORD")},
            )
        },
    )
    return PreparedProviderInput(
        "places", frozenset({"a", "b"}), registry["places"]
    ), scope


@pytest.mark.parametrize("name", ["base_table", "existing_view", "materialized_view"])
def test_exact_ids_and_schema_qualified_read(name: str, backend: Mock) -> None:
    provider = type("Source", (Places,), {"table": name})
    selection, scope = setup(provider)
    with scope.activate():
        acquired = acquire_provider_input(selection, scope)
        pd.testing.assert_frame_equal(acquired.frame, frame()[acquired.frame.columns])
        backend.table.assert_called_once_with(name, database="city data")
        expression = backend.to_pyarrow_batches.call_args.args[0]
        assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}
        sql = Backend().compile(expression, limit=None)
        assert '"city data"."places"' in sql
        assert " IN ('a', 'b')" in sql
        assert "LIMIT" not in sql
        assert set(expression.columns) == set(
            ProviderSchema(Places.schema).schema.columns
        )
        backend.disconnect.assert_not_called()
    backend.disconnect.assert_called_once()
    assert {call[0] for call in backend.mock_calls} <= {
        "reconnect",
        "table",
        "to_pyarrow_batches",
        "disconnect",
    }


@pytest.mark.parametrize(
    "ids", [["a"], ["a", "b", "c"], ["a", "a"], ["", "b"], [None, "b"], [1, 2]]
)
def test_bad_ids_use_producer_boundary(ids: list, backend: Mock) -> None:
    data = frame().iloc[[0] * len(ids)].copy()
    data["location_id"] = ids
    return_records(backend, data)
    selection, scope = setup()
    with scope.activate(), pytest.raises(LocationProviderContractError):
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "geometry", [None, Point(), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])]
)
def test_invalid_geometry_uses_producer_boundary(
    geometry: object, backend: Mock
) -> None:
    data = frame()
    data["geometry"] = gpd.GeoSeries([geometry, Point(1, 1)], crs=data.crs)
    return_records(backend, data)
    selection, scope = setup()
    with scope.activate(), pytest.raises(LocationProviderContractError):
        acquire_provider_input(selection, scope)


@pytest.mark.parametrize("conflict", ["crs", "srid", "encoding", "type"])
def test_incompatible_source_fails_acquisition(conflict: str, backend: Mock) -> None:
    data = frame()
    if conflict == "crs":
        data = data.to_crs(3857)
    elif conflict == "srid":
        data["geometry"] = gpd.GeoSeries(
            [shapely.set_srid(Point(0, 0), 3857), Point(1, 1)], crs=data.crs
        )
    elif conflict == "encoding":
        data = pd.DataFrame(data)
        data["geometry"] = [b"invalid", b"invalid"]
    else:
        schema = dict(backend.table.return_value.schema().items())
        schema["location_id"] = dt.int64
        backend.table.return_value = ibis.table(schema)
    return_records(backend, data)
    selection, scope = setup()
    with scope.activate(), pytest.raises(LocationAcquisitionError):
        acquire_provider_input(selection, scope)
    backend.disconnect.assert_called_once()


def test_empty_and_all_null_types(backend: Mock) -> None:
    selection, scope = setup()
    with scope.activate():
        empty = acquire_provider_input(selection, scope, needed_ids=[])
        assert not scope.instances
        assert empty.frame.crs == frame().crs
        assert str(empty.frame["count"].dtype) == "Int64"
        data = frame()
        data["count"] = [None, None]
        return_records(backend, data)
        result = acquire_provider_input(selection, scope)
        assert result.frame["count"].isna().all()
        assert str(result.frame["count"].dtype) == "Int64"
        return_records(backend, data.iloc[:0])
        provider = cast("Places", scope.get("places_binding"))
        decoded = provider.resolve(["missing"])
        pd.testing.assert_frame_equal(decoded, empty.frame)
        with pytest.raises(LocationProviderContractError):
            acquire_provider_input(selection, scope)


@pytest.mark.parametrize(
    "changes",
    [{"db_schema": ""}, {"table": None}, {"__annotations__": {"database": BaseIO}}],
)
def test_invalid_declarations(changes: dict, backend: Mock) -> None:
    with pytest.raises(PluginDefinitionError):
        setup(type("Invalid", (Places,), changes))
    backend.reconnect.assert_not_called()


def test_discovery_is_service_free_and_identity_ignores_binding(backend: Mock) -> None:
    selection, _ = setup()
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources={"alias": Places, "database": PostgresIO})}
    )
    registered = registry["places"]
    assert registered.query_capable
    assert registered.query is not None
    assert isinstance(registered.query.target_relation(), ir.Table)
    assert registered.scope == selection.provider.scope
    assert registered.declaration.query_schema is not None
    assert not backend.mock_calls


class CityDatabase(PostgresIO):
    def read_count(self) -> int:
        return cast("int", self.backend.execute(self.backend.table("places").count()))


class Reader(BaseIO):
    database: Annotated[CityDatabase, Resource("database")]

    def close(self) -> None:
        assert self.database.read_count() == 2


def test_providers_metric_and_resource_share_owner(backend: Mock) -> None:

    second = type("Second", (Places,), {"id": "second"})

    @metric(
        name="shared",
        code_revision="1",
        description="Read two providers and the database",
        origins=Places.origins,
        inputs={
            "first": LocationDependency(provider="places"),
            "second": LocationDependency(provider="second"),
        },
        outputs={
            "counts": pandera.DataFrameSchema(
                {
                    "value": pandera.Column(
                        int, description="Count", metadata={"unit": None}
                    )
                },
                strict=True,
            )
        },
    )
    def shared(
        first: gpd.GeoDataFrame,
        second: gpd.GeoDataFrame,
        database: Annotated[CityDatabase, Resource("database")],
        reader: Annotated[Reader, Resource("reader")],
    ) -> pd.DataFrame:
        assert reader.database is database
        assert database.backend is backend
        backend.disconnect.assert_not_called()
        return pd.DataFrame({"value": [len(first), len(second), database.read_count()]})

    backend.execute.side_effect = lambda expression, **_kwargs: (
        frame() if isinstance(expression, ir.Table) else 2
    )
    _, scope = setup()
    runner = LocalRunner.from_definitions(
        {
            "city": PluginDefinition(
                metrics=[shared],
                resources={
                    "first": Places,
                    "second": second,
                    "reader": Reader,
                    "database": CityDatabase,
                },
            )
        },
        resource_config=scope.configuration,
    )
    result = runner.run(
        "shared",
        inputs={
            "first": {"provider": "places", "ids": ["a", "b"]},
            "second": {"provider": "second", "ids": ["a", "b"]},
        },
    )
    assert result.tables["counts"].value.tolist() == [2, 2, 2]
    backend.reconnect.assert_called_once()
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize("nullable", [False, True])
def test_boolean_nulls_are_preserved_for_validation(
    backend: Mock, *, nullable: bool
) -> None:
    schema = declaration()
    schema.columns["active"] = pandera.Column(
        "boolean" if nullable else bool,
        nullable=nullable,
        description="Active",
        metadata={"unit": None},
    )
    provider = type("WithBoolean", (Places,), {"schema": schema})
    backend.table.return_value = ibis.table(
        compile_query_schema(ProviderSchema(schema).schema).schema, name="places"
    )
    data = frame()
    data["active"] = pd.Series([None, None], dtype=object)
    return_records(backend, data)
    selection, scope = setup(provider)
    with scope.activate():
        if nullable:
            result = acquire_provider_input(selection, scope)
            assert result.frame.active.isna().all()
            assert str(result.frame.active.dtype) == "boolean"
        else:
            with pytest.raises(LocationProviderContractError):
                acquire_provider_input(selection, scope)


@pytest.mark.parametrize(
    "kind", ["all", "chained", "nulls", "intersection", "distance"]
)
def test_selection_binds_complete_predicates(
    kind: str, backend: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection, scope = setup()
    query = selection.provider.query
    assert query is not None
    target = query.target_relation()
    literal = ibis.literal(Point(1, 2), type=dt.Point(srid=4326))
    if kind == "all":
        expression = target
    elif kind == "chained":
        intermediate = target.filter(target["count"] > 0)
        expression = intermediate.filter(
            intermediate.location_id.isin(["a", "b"]), intermediate["count"] <= 10
        )
    elif kind == "nulls":
        expression = target.filter(
            ops.IsNull(target["count"].op()).to_expr() | ~(target["count"] <= 5),
            ops.NotNull(target.location_id.op()).to_expr(),
        )
    elif kind == "intersection":
        expression = target.filter(
            target["count"] > 5, target.geometry.intersects(literal)
        )
    else:
        expression = target.filter(
            target["count"] > 5, target.geometry.d_within(literal, distance=100)
        )
    validate_selector_expression(expression, target)
    compiler = Backend()
    original = compiler.compile(expression, limit=None)
    monkeypatch.setattr(compiler, "get_schema", Mock(return_value=target.schema()))
    backend.table.return_value = compiler.table("places", database="city data")
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    with scope.activate():
        acquired = acquire_provider_candidates(selection.provider, expression, scope)
        bound = backend.to_pyarrow_batches.call_args.args[0]
        sql = compiler.compile(bound, limit=None)
        tree = sg.parse_one(sql, dialect="postgres")
        before = sg.parse_one(original, dialect="postgres")
        assert {(table.db, table.name) for table in tree.find_all(sge.Table)} == {
            ("city data", "places")
        }

        # Relation aliases are compiler choices; compare the full predicates
        # after removing only column qualifiers.
        def predicates(node: sge.Expr) -> list[str]:
            return [
                where.transform(
                    lambda part: (
                        sge.column(part.name) if isinstance(part, sge.Column) else part
                    )
                ).sql()
                for where in node.find_all(sge.Where)
            ]

        assert predicates(tree) == predicates(before)
        assert bound.schema() == ibis.schema(
            {**dict(expression.schema().items()), "geometry": dt.binary}
        )
        assert compiler.compile(expression, limit=None) == original
        assert tree.find(sge.Limit) is None
        assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}
        assert acquired.scope == selection.provider.scope
        pd.testing.assert_frame_equal(acquired.frame, frame()[acquired.frame.columns])
        acquire_provider_input(selection, scope)
        assert backend.table.call_args_list == [
            call("places", database="city data"),
            call("places", database="city data"),
        ]
        backend.disconnect.assert_not_called()
    backend.disconnect.assert_called_once()
    assert {entry[0] for entry in backend.mock_calls} <= {
        "reconnect",
        "table",
        "to_pyarrow_batches",
        "disconnect",
    }


@pytest.mark.parametrize("size", [0, 2, 10001])
def test_selection_complete_and_typed_results(size: int, backend: Mock) -> None:
    selection, scope = setup()
    query = selection.provider.query
    assert query is not None
    data = frame().iloc[[0] * size].reset_index(drop=True)
    data["location_id"] = [f"p{i}" for i in range(size)]
    data["count"] = None
    return_records(backend, data)
    with scope.activate():
        result = acquire_provider_candidates(
            selection.provider, query.target_relation(), scope
        )
    assert len(result.frame) == size
    assert result.frame["count"].isna().all()
    assert str(result.frame["count"].dtype) == "Int64"
    assert result.frame.crs == frame().crs
    assert backend.to_pyarrow_batches.call_args.kwargs == {"limit": None}


@pytest.mark.parametrize(
    ("x", "height", "matches"),
    [(0, 6, True), (100, 6, True), (101, 6, False), (0, 5, False), (0, None, False)],
    ids=["inside", "boundary", "outside", "height-threshold", "null"],
)
def test_spatial_expected_results_and_authoritative_forwarding(
    x: int, height: int | None, *, matches: bool, backend: Mock
) -> None:
    # Expected PostgreSQL result fixtures, paired with native compilation below.
    # This does not execute spatial predicates or simulate a database engine.
    projected = projected_places()
    target_selection, scope = setup(projected)
    target = target_selection.provider
    assert target.query is not None
    backend.table.return_value = ibis.table(
        target.query.schema, name="places", database="city data"
    )
    return_records(
        backend,
        gpd.GeoDataFrame(
            {"location_id": ["tree"], "count": pd.Series([height], dtype="Int64")},
            geometry=[Point(x, 0)],
            crs=3857,
        ).iloc[: 1 if matches else 0],
    )
    mapped = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        assert source.crs is not None
        assert source.crs.to_epsg() == 4326
        projected_source = source.to_crs(3857)
        area = ibis.literal(
            projected_source.geometry.union_all(), type=dt.GeoSpatial(srid=3857)
        )
        return relation.filter(
            relation["count"] > 5, relation.geometry.d_within(area, distance=100)
        )

    @location_operation(
        name="nearby",
        code_revision="1",
        description="Nearby tall trees",
        inputs={"source": LocationDependency(provider=SourcePlaces.id)},
        target_provider="places",
        target_selector=selector,
    )
    def mapping(
        source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        assert source.crs is not None
        assert source.crs.to_epsg() == 4326
        assert candidates.crs is not None
        assert candidates.crs.to_epsg() == 3857
        assert source.to_crs(candidates.crs).geometry.iloc[0] == Point(0, 0)
        mapped.append(True)
        ids = tuple(candidates.location_id)
        candidates["count"] = 999
        candidates.geometry = [Point(99, 99)] * len(candidates)
        return {"a": ids}

    definition = PluginDefinition(
        location_operations=[mapping],
        resources={
            "source": SourcePlaces,
            "places_binding": projected,
            "database": PostgresIO,
        },
    )
    registry = compile_provider_registry({"test": definition})
    binding = BoundPlugin(definition)
    source = NativeLocationInput(
        frame().iloc[:1], registry[SourcePlaces.id].contract.reference
    )
    invocation = binding.prepare_resolved_invocation("nearby", {"source": source})
    resolver = LocationResolver(scope, providers=registry)
    with scope.activate():
        result = execute_operation(binding, invocation, resolver)
        forwarded = forward_correspondences(result, target.schema.metadata)
        assert list(forwarded.frame.location_id) == (["tree"] if matches else [])
        assert forwarded.frame.crs is not None
        assert forwarded.frame.crs.to_epsg() == 3857
        if matches:
            assert forwarded.frame.geometry.iloc[0] == Point(x, 0)
            assert forwarded.frame["count"].iloc[0] == height
            reused = resolver.resolve(
                {"later": LocationSelection(provider="places", ids=["tree"])}
            )["later"]
            assert reused.frame.geometry.iloc[0] == Point(x, 0)
        assert source.frame.crs is not None
        assert source.frame.crs.to_epsg() == 4326
        assert source.frame.geometry.iloc[0] == Point(0, 0)
        assert mapped == [True]
        backend.to_pyarrow_batches.assert_called_once()
        sql = Backend().compile(
            backend.to_pyarrow_batches.call_args.args[0], limit=None
        )
        assert '"count" > 5' in sql
        assert "ST_DWITHIN" in sql
        assert "100.0" in sql
        assert "ST_GEOMFROMTEXT('POINT (0 0)', 3857)" in sql
    backend.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["binding", "compile", "execute", "decode", "schema", "srid"]
)
def test_postgis_selection_failures_do_not_reach_mapping(
    failure: str, backend: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapped = []

    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        del source
        literal = ibis.literal(
            Point(0, 0), type=dt.Point(srid=3857 if failure == "srid" else 4326)
        )
        return relation.filter(relation.geometry.intersects(literal))

    @location_operation(
        name="failure",
        code_revision="1",
        description="Propagate acquisition errors",
        inputs={"source": LocationDependency(provider=SourcePlaces.id)},
        target_provider="places",
        target_selector=selector,
    )
    def mapping(
        source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame
    ) -> dict[str, tuple[str, ...]]:
        del source, candidates
        mapped.append(True)
        return {}

    definition = PluginDefinition(
        location_operations=[mapping],
        resources={
            "source": SourcePlaces,
            "places_binding": Places,
            "database": PostgresIO,
        },
    )
    registry = compile_provider_registry({"test": definition})
    binding = BoundPlugin(definition)
    source = NativeLocationInput(frame(), registry[SourcePlaces.id].contract.reference)
    invocation = binding.prepare_resolved_invocation("failure", {"source": source})
    _, scope = setup()
    if failure == "binding":
        monkeypatch.setattr(ir.Table, "select", Mock(side_effect=ValueError("binding")))
    elif failure == "compile":
        compiler = Backend()
        monkeypatch.setattr(
            compiler,
            "compile",
            Mock(side_effect=NotImplementedError("unsupported backend operation")),
        )
        backend.to_pyarrow_batches.side_effect = compiler.compile
    elif failure == "execute":
        backend.to_pyarrow_batches.side_effect = RuntimeError("execution failed")
    elif failure == "decode":
        return_records(
            backend,
            pd.DataFrame(
                {"location_id": ["a"], "count": [1], "geometry": [b"invalid"]}
            ),
        )
    elif failure == "schema":
        return_records(backend, frame().assign(location_id=["a", "a"]))
    expected = (
        ValueError
        if failure == "srid"
        else LocationProviderContractError
        if failure == "schema"
        else LocationAcquisitionError
    )
    with scope.activate(), pytest.raises(expected):
        execute_operation(
            binding, invocation, LocationResolver(scope, providers=registry)
        )
    assert not mapped
    if failure == "srid":
        backend.reconnect.assert_not_called()
        backend.to_pyarrow_batches.assert_not_called()
    else:
        backend.disconnect.assert_called_once()


def projected_places() -> type[Places]:
    schema = declaration()
    schema.columns["geometry"].dtype = Geometry(crs="EPSG:3857")
    return type("Projected", (Places,), {"schema": schema})


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


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("kind", ["mixed", "all_null", "empty"])
def test_native_batches_preserve_values_and_forwarding(
    monkeypatch: pytest.MonkeyPatch, *, selected: bool, kind: str
) -> None:
    schema = declaration()
    schema.columns["active"] = pandera.Column(
        "boolean", nullable=True, description="Active", metadata={"unit": None}
    )
    provider = type("WithBoolean", (Places,), {"schema": schema})
    connection, cursor = native_postgres(monkeypatch, provider)
    values = [2**53 + 1, -(2**53 + 1), 2**63 - 1, -(2**63), None]
    if kind == "all_null":
        values = [None] * len(values)
    elif kind == "empty":
        values = []
    ids = [f"p{i}" for i in range(len(values))]
    geometry = shapely.set_srid(Point(1, 2), 4326)
    binary = memoryview(shapely.to_wkb(geometry, include_srid=True))
    rows = [(key, binary, count, None) for key, count in zip(ids, values, strict=True)]
    cursor.execute.return_value.fetchmany.side_effect = (
        [rows[:2], rows[2:], []] if rows else [[]]
    )
    selection, scope = setup(provider)
    mapped = Mock()

    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    with scope.activate():
        if selected:
            output = selected_records(scope, provider, ids, mapped)
            mapped.assert_called_once()
        elif rows:
            output = acquire_provider_input(
                PreparedProviderInput("places", frozenset(ids), selection.provider),
                scope,
            ).frame
        else:
            # Nonempty exact requests still materialize a typed empty result;
            # the ordinary boundary will reject missing requested IDs.
            instance = cast("Places", scope.get("places_binding"))
            output = selection.provider.schema.validate(instance.resolve(["missing"]))
        pd.testing.assert_series_equal(
            output["count"].reset_index(drop=True),
            pd.Series(values, dtype="Int64", name="count"),
        )
        assert str(output.active.dtype) == "boolean"
        assert output.active.isna().all()
        assert output.crs is not None
        assert output.crs.to_epsg() == 4326
        assert output.geometry.tolist() == [geometry] * len(values)
        assert shapely.get_srid(output.geometry.to_numpy()).tolist() == [4326] * len(
            values
        )
        connection.close.assert_not_called()
    connection.close.assert_called_once()
    connection.cursor.return_value.__exit__.assert_called_once()
    cursor.execute.assert_called_once()  # Forwarding must reuse selected records.
    sql = cursor.execute.call_args.args[0]
    assert '"city data"."places"' in sql
    assert "ST_ASEWKB" in sql
    assert "LIMIT" not in sql
    assert "WHERE" in sql


@pytest.mark.parametrize("failure", ["query", "later_batch", "wkb", "srid"])
def test_native_batch_failures_close_without_mapping(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    connection, cursor = native_postgres(monkeypatch)
    point = shapely.set_srid(Point(1, 2), 3857 if failure == "srid" else 4326)
    binary = b"broken" if failure == "wkb" else shapely.to_wkb(point, include_srid=True)
    row = ("a", memoryview(binary), 2**53 + 1)
    if failure == "query":
        cursor.execute.side_effect = RuntimeError("query failed")
    else:
        cursor.execute.return_value.fetchmany.side_effect = [
            [row],
            RuntimeError("batch failed") if failure == "later_batch" else [],
        ]
    mapped = Mock()

    _, scope = setup()
    with scope.activate(), pytest.raises(LocationAcquisitionError):
        selected_records(scope, Places, None, mapped)
    mapped.assert_not_called()
    connection.cursor.return_value.__exit__.assert_called_once()
    connection.close.assert_called_once()


def selected_records(
    scope: ResourceScope, provider: type[Places], ids: list[str] | None, mapped: Mock
) -> gpd.GeoDataFrame:
    def selector(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        del source
        return (
            relation if ids is None else relation.filter(relation.location_id.isin(ids))
        )

    @location_operation(
        name="precise",
        code_revision="1",
        description="Forward precise records",
        inputs={"source": LocationDependency(provider=SourcePlaces.id)},
        target_provider="places",
        target_selector=selector,
    )
    def mapping(source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> dict:
        mapped(candidates)
        return dict.fromkeys(source.location_id, tuple(candidates.location_id))

    definition = PluginDefinition(
        location_operations=[mapping],
        resources={
            "source": SourcePlaces,
            "places_binding": provider,
            "database": PostgresIO,
        },
    )
    registry = compile_provider_registry({"test": definition})
    binding = BoundPlugin(definition)
    source = NativeLocationInput(
        frame().iloc[:1], registry[SourcePlaces.id].contract.reference
    )
    invocation = binding.prepare_resolved_invocation("precise", {"source": source})
    result = execute_operation(
        binding, invocation, LocationResolver(scope, providers=registry)
    )
    return forward_correspondences(result, registry["places"].schema.metadata).frame
