from asyncio import CancelledError
from typing import cast
from unittest.mock import MagicMock, Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest
import shapely
import sqlglot as sg
from psycopg.errors import InsufficientPrivilege
from sqlglot import expressions as sge

from lyra.contracts.errors import LocationAcquisitionError
from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.runtime.results import RunResult
from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    PostgresIO,
    location_operation,
    metric,
)
from lyra.sdk.interventions import accept_interventions
from lyra.sdk.provider_schema import ProviderSchema
from test_support.location_operations import Places as Sources
from test_support.location_schema import column
from tests.cache_helpers import SCHEMA
from tests.postgis_provider_helpers import Places, database_config, native_postgres
from tests.provider_schema_helpers import declaration, frame
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


class Trees(Places):
    schema = declaration()
    schema.columns.update(
        {
            "height": column("float64"),
            "active": column("boolean", nullable=True),
        }
    )


POINT = shapely.Point(0.0003144543032703886, 1.2345678901234567)


def proposal() -> gpd.GeoDataFrame:
    return frame().assign(
        location_id=["007", "δ'2"],
        geometry=gpd.GeoSeries([POINT, shapely.Point(2, 3, 4)], crs=4326),
        count=pd.Series([-(2**63), 2**63 - 1], dtype="Int64"),
        height=[1.2345678901234567, -0.0003144543032703886],
        active=pd.Series([None, None], dtype="boolean"),
    )


def change(catalog: str = Places.id, count: int = 42) -> Intervention:
    return Intervention(
        id="plan",
        catalog=catalog,
        revision=str(count),
        add=frame()
        .iloc[:1]
        .assign(location_id="007", count=pd.Series([count], dtype="Int64")),
        remove=["a"],
    )


def resolver_for(resources: dict, changes: list[Intervention]) -> LocationResolver:
    registry = compile_provider_registry(
        {"city": PluginDefinition(resources=resources)}
    )
    return LocationResolver(
        ResourceScope(resources, database_config()),
        providers=registry,
        interventions=accept_interventions(
            changes, {key: p.schema for key, p in registry.items()}
        ),
    )


def target(resolver: LocationResolver, catalog: str = Places.id) -> ir.Table:
    query = resolver.providers[catalog].query
    assert query is not None
    return query.target_relation()


def statements(cursor: MagicMock, prefix: str) -> list[str]:
    return [
        sql
        for entry in cursor.execute.call_args_list
        if (sql := entry.args[0]).startswith(prefix)
    ]


def table_names(sql: list[str]) -> list[tuple[str, str]]:
    return [
        (table.db, table.name)
        for statement in sql
        for table in sg.parse_one(statement, dialect="postgres").find_all(sge.Table)
    ]


def set_result(cursor: MagicMock, rows: list[tuple]) -> None:
    cursor.execute.return_value.fetchmany.side_effect = [rows, []] if rows else [[]]


def rows(data: gpd.GeoDataFrame, *, include_srid: bool = False) -> list[tuple]:
    geometry = data.geometry.to_numpy()
    if include_srid:
        geometry = shapely.set_srid(geometry, 4326)
    return list(
        zip(
            data.location_id,
            shapely.to_wkb(geometry, include_srid=include_srid),
            pa.array(data["count"]).to_pylist(),
            data.height,
            pa.array(data.active).to_pylist(),
            strict=True,
        )
    )


def assert_staged(
    cursor: MagicMock, added: gpd.GeoDataFrame, removals: list[str]
) -> tuple[tuple[str, str], tuple[str, str]]:
    ddl = statements(cursor, "CREATE")
    additions_ddl, removals_ddl = sorted(ddl, key=lambda sql: '"geometry"' not in sql)
    added_name, removed_name = table_names([additions_ddl, removals_ddl])
    assert added_name[0] == removed_name[0] == "pg_temp"
    assert added_name != removed_name
    assert all(sql.startswith("CREATE TEMPORARY TABLE") for sql in ddl)
    assert '"count" BIGINT' in additions_ddl
    assert '"active" BOOLEAN' in additions_ddl
    assert '"geometry" BYTEA' in additions_ddl
    assert '"height" DOUBLE PRECISION' in additions_ddl
    writes = cursor.copy.return_value.__enter__.return_value.write_row
    assert sorted(c.args[0] for c in writes.call_args_list) == sorted(
        [
            *rows(added),
            *((key,) for key in removals),
        ]
    )
    assert set(table_names([c.args[0] for c in cursor.copy.call_args_list])) == {
        *([added_name] if len(added) else []),
        *([removed_name] if removals else []),
    }
    return added_name, removed_name


def check_selection_sql(
    cursor: MagicMock, added_name: tuple[str, str], removed_name: tuple[str, str]
) -> None:
    all_sql, filtered_sql = statements(cursor, "SELECT")
    for sql in (all_sql, filtered_sql):
        tree = sg.parse_one(sql, dialect="postgres")
        union = tree.find(sge.Union)
        assert union is not None
        assert not union.args["distinct"]
        assert set(table_names([union.left.sql("postgres")])) == {
            ("city data", "places"),
            removed_name,
        }
        assert table_names([union.right.sql("postgres")]) == [added_name]
        exclusion = union.left.find(sge.Not)
        assert exclusion is not None
        membership = exclusion.find(sge.In)
        assert membership is not None
        assert membership.this.name == "location_id"
        assert table_names([membership.sql("postgres")]) == [removed_name]
        assert "ST_GEOMFROMWKB" in union.right.sql("postgres")
        assert "4326" in union.right.sql("postgres")
        outer = union.find_ancestor(sge.Select)
        assert outer is not None
        if sql == all_sql:
            assert outer.args.get("where") is None
        else:
            predicate = " ".join(
                where.sql("postgres")
                for where in tree.find_all(sge.Where)
                if where.find_ancestor(sge.Union) is None
            )
            for clause in (
                '"count" > 0',
                '"active" IS NULL',
                "ST_DWITHIN",
                "10.0",
                (
                    "ST_GEOMFROMTEXT('POINT (0.0003144543032703886 "
                    "1.2345678901234567)', 4326)"
                ),
            ):
                assert clause in predicate
        assert tree.find(sge.Limit) is None


@pytest.mark.parametrize("boolean_type", ["bool", "boolean"])
@pytest.mark.parametrize("actions", ["add", "remove", "both"])
def test_selection_uses_typed_staging_and_complete_predicate(
    monkeypatch: pytest.MonkeyPatch, actions: str, boolean_type: str
) -> None:
    monkeypatch.setattr(
        Trees,
        "schema",
        ProviderSchema(Trees.schema).schema.update_column("active", dtype=boolean_type),
    )
    connection, cursor = native_postgres(monkeypatch, Trees)
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    added = proposal().iloc[: 0 if actions == "remove" else 2]
    removals = [] if actions == "add" else ["a"]
    resolver = resolver_for(
        {"trees": Trees, "database": PostgresIO},
        [
            Intervention(
                id="plan", catalog=Trees.id, revision="1", add=added, remove=removals
            )
        ],
    )
    relation = target(resolver)
    first = relation.filter(
        ops.IsNull(relation.active.op()).to_expr(), relation["count"] > 0
    )
    literal = ibis.literal(POINT, type=dt.Point(srid=4326))
    filtered = first.filter(first.geometry.d_within(literal, distance=10))
    validate_selector_expression(filtered, relation)
    with resolver.scope.activate():
        physical = {
            **dict(relation.schema()),
            "count": dt.int32,
            "geometry": dt.Point(srid=4326),
        }
        monkeypatch.setattr(
            cast("PostgresIO", resolver.scope.get("database")).backend,
            "get_schema",
            Mock(return_value=ibis.schema(physical)),
        )
        base = frame().assign(
            height=[2.0, 3.0], active=pd.Series([True, None], dtype="boolean")
        )
        all_rows = pd.concat([base.iloc[len(removals) :], added], ignore_index=True)
        # The cursor supplies server results. Native SQL below checks where both
        # branches and every predicate execute; Python does no spatial filtering.
        for expression, expected in [
            (relation, all_rows),
            (
                filtered,
                added.iloc[1:].astype({"active": boolean_type})
                if added.empty
                else added.iloc[1:],
            ),
        ]:
            set_result(cursor, rows(expected, include_srid=True))
            selected = resolver.select(Trees.id, expression)
            pd.testing.assert_frame_equal(
                selected.frame, expected[selected.frame.columns].reset_index(drop=True)
            )
            assert str(selected.frame.active.dtype) == (
                boolean_type if expected.empty else "boolean"
            )
            assert selected.added_ids == set(expected.location_id).intersection(
                added.location_id
            )
        staged = assert_staged(cursor, added, removals)
        check_selection_sql(cursor, *staged)
        connection.close.assert_not_called()
    assert sorted(table_names(statements(cursor, "DROP"))) == sorted(staged)
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["privilege", "second_create", "copy", "query", "cancel", "cleanup"]
)
def test_failure_cleans_only_owned_tables_and_preserves_primary(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    connection, cursor = native_postgres(monkeypatch)
    resolver = resolver_for({"places": Places, "database": PostgresIO}, [change()])
    primary = (
        InsufficientPrivilege("TEMPORARY denied")
        if failure == "privilege"
        else (
            CancelledError()
            if failure == "cancel"
            else RuntimeError("acquisition failed")
        )
    )
    cleanup_error = RuntimeError("drop failed")
    owned = []
    dropped = []

    def execute(sql: str) -> Mock:
        connection.close.assert_not_called()
        if sql.startswith("CREATE"):
            if failure == "privilege" or (failure == "second_create" and owned):
                raise primary
            owned.extend(table_names([sql]))
        elif sql.startswith("DROP"):
            dropped.extend(table_names([sql]))
            raise cleanup_error
        elif failure in {"query", "cancel"}:
            raise primary
        return Mock(fetchmany=Mock(return_value=[]))

    cursor.execute.side_effect = execute
    if failure == "copy":
        cursor.copy.return_value.__enter__.return_value.write_row.side_effect = primary
    expected = {"cleanup": RuntimeError, "cancel": CancelledError}.get(
        failure, LocationAcquisitionError
    )
    if failure == "privilege":
        instance = cast("Places", resolver.scope.get("places"))
        assert instance.existing_ids(["a"]) == set()
        assert instance.resolve(["a"]).empty
        assert instance.select(
            target(resolver), resolver.providers[Places.id].schema
        ).empty
        assert not statements(cursor, "CREATE")
    with pytest.raises(expected) as caught, resolver.scope.activate():
        resolver.select(Places.id, target(resolver))
    assert (
        caught.value is (cleanup_error if failure == "cleanup" else primary)
        or caught.value.__cause__ is primary
    )
    assert len(owned) == {"privilege": 0, "second_create": 1, "copy": 1}.get(failure, 2)
    assert sorted(dropped) == sorted(owned)
    connection.close.assert_called_once()


def selected_change(
    resolver: LocationResolver, cursor: MagicMock, catalog: str, count: int
) -> set[tuple[str, str]]:
    set_result(cursor, [("007", frame().geometry.iloc[0].wkb, count)])
    relation = target(resolver, catalog)
    result = resolver.select(catalog, relation.filter(relation.location_id == "007"))
    assert result.frame["count"].tolist() == [count]
    assert result.added_ids == {"007"}
    return {
        name
        for name in table_names(statements(cursor, "SELECT")[-1:])
        if name[0] == "pg_temp"
    }


def test_two_catalogs_share_connection_and_drop_only_their_own_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Other(Places):
        id = "other"
        db_schema = "other city"

    connection, cursor = native_postgres(monkeypatch)
    resolver = resolver_for(
        {"first": Places, "second": Other, "database": PostgresIO},
        [change(), change(Other.id, 81)],
    )
    with resolver.scope.activate():
        first = selected_change(resolver, cursor, Places.id, 42)
        second = selected_change(resolver, cursor, Other.id, 81)
        assert len(first) == len(second) == 2
        assert first.isdisjoint(second)
        assert set(table_names(statements(cursor, "CREATE"))) == first | second
        assert ("other city", "places") in table_names(
            statements(cursor, "SELECT")[-1:]
        )
        provider = cast("Places", resolver.scope.get("first"))
        provider.close()
        assert set(table_names(statements(cursor, "DROP"))) == first
        assert selected_change(resolver, cursor, Other.id, 81) == second
        # Ordinary access continues to use the base on the still-open backend.
        set_result(cursor, [("a",)])
        assert provider.existing_ids(["a", "007"]) == {"a"}
        assert table_names(statements(cursor, "SELECT")[-1:]) == [
            ("city data", "places")
        ]
        connection.close.assert_not_called()
    assert sorted(table_names(statements(cursor, "DROP"))) == sorted(first | second)
    connection.close.assert_called_once()


@pytest.mark.parametrize("overlap", [False, True], ids=["successive", "overlapping"])
def test_each_request_stages_its_own_values(
    monkeypatch: pytest.MonkeyPatch, *, overlap: bool
) -> None:
    resources = {"places": Places, "database": PostgresIO}
    first_connection, first_cursor = native_postgres(monkeypatch)
    first = resolver_for(resources, [change()])

    def another_request() -> set[tuple[str, str]]:
        connection, cursor = native_postgres(monkeypatch)
        second = resolver_for(resources, [change(count=97)])
        with second.scope.activate():
            names = selected_change(second, cursor, Places.id, 97)
            writes = cursor.copy.return_value.__enter__.return_value.write_row
            assert writes.call_args_list[0].args[0] == (
                "007",
                frame().geometry.iloc[0].wkb,
                97,
            )
        assert set(table_names(statements(cursor, "DROP"))) == names
        connection.close.assert_called_once()
        return names

    with first.scope.activate():
        names = selected_change(first, first_cursor, Places.id, 42)
        writes = first_cursor.copy.return_value.__enter__.return_value.write_row
        assert writes.call_args_list[0].args[0] == (
            "007",
            frame().geometry.iloc[0].wkb,
            42,
        )
        if overlap:
            assert names.isdisjoint(another_request())
            assert selected_change(first, first_cursor, Places.id, 42) == names
            first_connection.close.assert_not_called()
    assert set(table_names(statements(first_cursor, "DROP"))) == names
    first_connection.close.assert_called_once()
    if not overlap:
        assert names.isdisjoint(another_request())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_operation_publication_after_staging_cleanup(
    monkeypatch: pytest.MonkeyPatch, database: Database, *, cleanup_fails: bool
) -> None:
    def selector(seeds: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
        return relation.filter(relation["count"] > len(seeds))

    @location_operation(
        name="nearby",
        description="Select matching places",
        code_revision="1",
        inputs={"seeds": LocationDependency(provider=Sources.id)},
        target_provider=Places.id,
        target_selector=selector,
        cache="full",
    )
    def nearby(seeds: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> dict:
        assert pool.location_id.tolist() == ["007"]
        assert pool["count"].tolist() == [42]
        pool["count"] = 0
        return dict.fromkeys(seeds.location_id, tuple(pool.location_id))

    @metric(
        name="counts",
        description="Read matched counts",
        code_revision="1",
        inputs={
            "seeds": LocationDependency(provider=Sources.id),
            "other": LocationDependency(provider=Sources.id),
            "matches": LocationDependency(operation="nearby").bind(
                seeds=Input("seeds")
            ),
            "other_matches": LocationDependency(operation="nearby").bind(
                seeds=Input("other")
            ),
        },
        outputs={"data": SCHEMA},
        cache="full",
    )
    def counts(
        seeds: gpd.GeoDataFrame,
        matches: gpd.GeoDataFrame,
        other: gpd.GeoDataFrame,
        other_matches: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert matches.source_location_id.tolist() == seeds.location_id.tolist()
        assert other_matches.source_location_id.tolist() == other.location_id.tolist()
        assert other_matches["count"].tolist() == [42] * len(other)
        return pd.DataFrame({"value": matches["count"].astype("int64")})

    connection, cursor = native_postgres(monkeypatch)
    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[counts],
                location_operations=[nearby],
                resources={"source": Sources, "places": Places, "database": PostgresIO},
            )
        },
        resource_config=database_config(),
    )
    # The skipped run still validates base membership, without staging. The
    # next run needs a selector; its result must outlive mapping's working copy.
    set_result(cursor, [("a",)])

    def run(ids: list[str]) -> RunResult:
        return runner.run(
            "counts",
            inputs={
                "seeds": {"provider": Sources.id, "ids": ids},
                "other": {"provider": Sources.id, "ids": sorted(ids)[:1]},
            },
            interventions=[change()],
        )

    empty = run([])
    assert empty.tables["data"].empty
    assert empty.report.intervention_validations[0].membership_lookup == "executed"
    assert not statements(cursor, "CREATE")
    prior_results = list(database.rows["final_results"])
    cleanup_error = RuntimeError("drop failed")

    def execute(sql: str) -> MagicMock:
        if cleanup_fails and sql.startswith("DROP"):
            raise cleanup_error
        return cursor.execute.return_value

    cursor.execute.side_effect = execute
    candidate = ("007", frame().geometry.iloc[0].wkb, 42)
    cursor.execute.return_value.fetchmany.side_effect = [
        [candidate],
        [],
        [candidate],
        [],
    ]
    if cleanup_fails:
        with pytest.raises(GraphExecutionError) as caught:
            run(["s", "t"])
        assert caught.value.__cause__ is cleanup_error
        assert database.rows["final_results"] == prior_results
    else:
        fresh = run(["s", "t"])
        reused = run(["t", "s"])
        for result, expected in ((fresh, "executed"), (reused, "skipped")):
            assert result.tables["data"].value.tolist() == [42, 42]
            (validation,) = result.report.intervention_validations
            assert validation.membership_lookup == "skipped"
            uses = [node.operation for node in result.report.nodes if node.operation]
            assert len(uses) == 2
            for operation in uses:
                assert operation.target_scope == validation.scope
                assert (
                    operation.selector
                    == operation.target_query
                    == operation.mapping
                    == expected
                )
    assert len(statements(cursor, "SELECT")) == 3
    assert len(statements(cursor, "CREATE")) == 2
    assert sorted(table_names(statements(cursor, "DROP"))) == sorted(
        table_names(statements(cursor, "CREATE"))
    )
    assert connection.close.call_count == 2
