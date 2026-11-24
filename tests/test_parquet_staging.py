from asyncio import CancelledError
from pathlib import Path
from unittest.mock import Mock

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
from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from lyra.contracts.errors import LocationAcquisitionError
from lyra.data.interventions import Intervention
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    ResourceConfig,
    location_operation,
    metric,
)
from lyra.sdk.interventions import accept_interventions
from lyra.sdk.provider_schema import ProviderSchema
from test_support.location_operations import Places as Sources
from tests.cache_helpers import SCHEMA
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

__all__ = ["backend_fixture", "source_fixture"]


def proposed_records() -> gpd.GeoDataFrame:
    data = records()
    data["location_id"] = ["007", "δ'2"]
    data["integer"] = pd.Series([-(2**63), 2**63 - 1], dtype="int64")
    data["count"] = pd.Series([2**63 - 1, None], dtype="Int64")
    data["number"] = [1.2345678901234567, -9.876543210987654]
    data.geometry = [
        shapely.Point(0.0003144543032703886, 1.2345678901234567),
        shapely.Point(1, 2),
    ]
    for name in ("label", "height", "active"):
        data[name] = pd.Series([None, None], dtype=data[name].dtype)
    return data


def check_effective_sql(sql: str, added_name: str, removed_name: str) -> sge.Select:
    tree = sg.parse_one(sql, dialect="duckdb")
    union = tree.find(sge.Union)
    assert union is not None
    assert not union.args["distinct"]
    assert {table.name for table in union.left.find_all(sge.Table)} == {
        "configured_file",
        removed_name,
    }
    assert {table.name for table in union.right.find_all(sge.Table)} == {added_name}
    for table in union.find_all(sge.Table):
        assert (table.catalog, table.db) == ("temp", "main")
    exclusion = union.left.find(sge.Not)
    assert exclusion is not None
    membership = exclusion.find(sge.In)
    assert membership is not None
    assert membership.this.name == "location_id"
    assert {table.name for table in membership.find_all(sge.Table)} == {removed_name}
    assert "ST_GEOMFROMWKB" in union.right.sql(dialect="duckdb").upper()
    outer = union.find_ancestor(sge.Select)
    assert isinstance(outer, sge.Select)
    return outer


@pytest.fixture
def native_cursor(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    # Native Ibis compiler and Arrow batch wrapper, with all connection I/O faked.
    native = Backend()
    connection = Mock()
    native.con = connection
    base = ibis.table(ibis.schema(binary_records().schema), name="configured_file")
    sql = Mock()
    monkeypatch.setattr(native, "reconnect", Mock())
    monkeypatch.setattr(native, "read_parquet", Mock(return_value=base))
    monkeypatch.setattr(native, "_run_pre_execute_hooks", Mock())
    monkeypatch.setattr(native, "raw_sql", sql)
    monkeypatch.setattr("ibis.backends.duckdb.Backend", Mock(return_value=native))
    monkeypatch.setattr(ibis.options.sql, "default_limit", 1)
    return connection, sql


def effective_resolver(source: Path, change: Intervention) -> LocationResolver:
    selected, scope = setup(source)
    registered = selected.provider
    return LocationResolver(
        scope,
        providers={Places.id: registered},
        interventions=accept_interventions([change], {Places.id: registered.schema}),
    )


def excluding_filter(target: ir.Table) -> ir.Table:
    point = ibis.literal(
        shapely.Point(0.0003144543032703886, 1.2345678901234567),
        type=dt.Point(srid=4326),
    )
    first = target.filter(target.flag, ops.IsNull(target.height.op()).to_expr())
    return first.filter(first.geometry.intersects(point), first.integer > 0)


def check_sql_and_loads(
    sql: str,
    loads: list,
    added: gpd.GeoDataFrame,
    removals: list[str],
    *,
    filtered: bool,
) -> None:
    assert len(loads) == 2
    (added_name, staged), (removed_name, removed) = [args.args for args in loads]
    assert staged.equals(binary_records(added))
    assert removed.schema == pa.schema([staged.schema.field("location_id")])
    assert removed["location_id"].to_pylist() == removals
    outer = check_effective_sql(sql, added_name, removed_name)
    assert "LIMIT" not in sql
    predicate = outer.args.get("where")
    if filtered:
        assert predicate is not None
        condition = predicate.sql(dialect="duckdb")
        for part in (
            '"flag"',
            '"height" IS NULL',
            '"integer" > 0',
            "ST_INTERSECTS",
            "ST_GEOMFROMTEXT('POINT (0.0003144543032703886 1.2345678901234567)')",
        ):
            assert part in condition
    else:
        assert predicate is None


@pytest.mark.parametrize("boolean_type", ["bool", "boolean"])
@pytest.mark.parametrize("action", ["add", "remove", "both"])
def test_staged_selection_compilation_and_materialization(
    source: Path,
    native_cursor: tuple[Mock, Mock],
    action: str,
    boolean_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Places,
        "schema",
        ProviderSchema(Places.schema).schema.update_column(
            "active", dtype=boolean_type
        ),
    )
    connection, sql = native_cursor
    added = proposed_records().iloc[: 0 if action == "remove" else 2]
    removals = [] if action == "add" else ["a"]
    resolver = effective_resolver(
        source,
        Intervention(
            id="plan", catalog=Places.id, revision="1", add=added, remove=removals
        ),
    )
    registered = resolver.providers[Places.id]
    assert registered.query is not None
    target = registered.query.target_relation()
    filtered = excluding_filter(target)
    original = Backend().compile(filtered, limit=None)
    all_rows = pd.concat([records().iloc[len(removals) :], added], ignore_index=True)
    with resolver.scope.activate():
        for expression, expected in (
            (target, all_rows),
            (filtered, registered.schema.empty_frame()),
        ):
            validate_selector_expression(expression, target)
            arrow = binary_records(expected)
            sql.return_value.fetch_record_batch.return_value = arrow.cast(
                transport_schema(arrow.schema)
            ).to_reader(max_chunksize=1)
            result = resolver.select(Places.id, expression)
            pd.testing.assert_frame_equal(result.frame, expected)
            assert result.added_ids == set(added.location_id).intersection(
                expected.location_id
            )
            check_sql_and_loads(
                sql.call_args.args[0],
                connection.register.call_args_list,
                added,
                removals,
                filtered=expression is filtered,
            )
        assert Backend().compile(filtered, limit=None) == original
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["first_register", "setup", "query", "cancel", "cleanup"]
)
def test_owned_staging_cleanup(source: Path, backend: Mock, failure: str) -> None:
    resolver = effective_resolver(
        source, Intervention(id="plan", catalog=Places.id, revision="1", remove=["a"])
    )
    registered = resolver.providers[Places.id]
    primary = CancelledError() if failure == "cancel" else RuntimeError("primary")
    cleanup = RuntimeError("disconnect failed")
    owned = []

    def register(name: str, _data: pa.Table) -> None:
        if failure == "first_register" or (failure == "setup" and owned):
            raise primary
        owned.append(name)

    backend.con.register.side_effect = register
    if failure in {"query", "cancel"}:
        backend.to_pyarrow_batches.side_effect = primary
    backend.disconnect.side_effect = cleanup
    assert registered.query is not None
    expected = {"cleanup": RuntimeError, "cancel": CancelledError}.get(
        failure, LocationAcquisitionError
    )
    with pytest.raises(expected) as caught, resolver.scope.activate():
        resolver.select(Places.id, registered.query.target_relation())
    error = caught.value
    if failure == "cleanup":
        assert error is cleanup
    else:
        assert error is primary or error.__cause__ is primary
    backend.disconnect.assert_called_once()


def test_staging_names_and_values_belong_to_each_request(
    source: Path, backend: Mock
) -> None:
    previous = set()
    for number in (1.25, 2.5):
        proposal = proposed_records().assign(number=number)
        resolver = effective_resolver(
            source,
            Intervention(
                id="plan",
                catalog=Places.id,
                revision=str(number),
                add=proposal,
            ),
        )
        registered = resolver.providers[Places.id]
        with resolver.scope.activate():
            assert registered.query is not None
            target = registered.query.target_relation()
            return_records(backend, binary_records(proposal))
            result = resolver.select(
                Places.id,
                target.filter(target.location_id.isin(proposal.location_id.tolist())),
            )
            assert result.frame.number.tolist() == [number, number]
            added, removed = backend.con.register.call_args_list
            names = {added.args[0], removed.args[0]}
            assert len(names) == 2
            assert names.isdisjoint(previous)
            assert added.args[1]["number"].to_pylist() == [number, number]
            previous.update(names)
        backend.disconnect.assert_called_once()
        backend.reset_mock()


def test_operation_stages_only_for_a_needed_selector(
    source: Path, backend: Mock
) -> None:
    def selector(seeds: gpd.GeoDataFrame, target: ir.Table) -> ir.Table:
        return target.filter(target.integer > 2 + len(seeds))

    @location_operation(
        name="nearby",
        description="Select matching places",
        code_revision="1",
        cache="full",
        inputs={"seeds": LocationDependency(provider=Sources.id)},
        target_provider=Places.id,
        target_selector=selector,
    )
    def nearby(seeds: gpd.GeoDataFrame, pool: gpd.GeoDataFrame) -> dict:
        assert pool.location_id.tolist() == ["δ'2"]
        assert pool.integer.tolist() == [2**63 - 1]
        pool["integer"] = 0
        return dict.fromkeys(seeds.location_id, tuple(pool.location_id))

    @metric(
        name="highest",
        description="Maximum selected integer",
        code_revision="1",
        cache="full",
        inputs={
            "seeds": LocationDependency(provider=Sources.id),
            "other": LocationDependency(provider=Sources.id),
            "other_matches": LocationDependency(operation="nearby").bind(
                seeds=Input("other")
            ),
            "matches": LocationDependency(operation="nearby").bind(
                seeds=Input("seeds")
            ),
        },
        outputs={"data": SCHEMA},
    )
    def highest(
        seeds: gpd.GeoDataFrame,
        matches: gpd.GeoDataFrame,
        other: gpd.GeoDataFrame,
        other_matches: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert list(matches.source_location_id) == list(seeds.location_id)
        assert list(other_matches.source_location_id) == list(other.location_id)
        assert set(other_matches.integer) == set(matches.integer)
        return pd.DataFrame({"value": [max(matches.integer, default=0)]})

    @metric(
        name="seed_count",
        description="Count seeds",
        code_revision="1",
        inputs={"seeds": LocationDependency(provider=Sources.id)},
        outputs={"data": SCHEMA},
    )
    def seed_count(seeds: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(seeds)]})

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                location_operations=[nearby],
                metrics=[highest, seed_count],
                resources={"source": Sources, "places": Places},
            )
        },
        resource_config={"places": ResourceConfig(config={"path": str(source)})},
    )
    changes = [
        Intervention(
            id="plan",
            catalog=Places.id,
            revision="1",
            add=proposed_records(),
            remove=["a"],
        )
    ]
    backend.to_pyarrow_batches.side_effect = [
        pa.table({"location_id": ["a"]}).to_reader(),
        binary_records(proposed_records().iloc[1:]).to_reader(),
        binary_records(proposed_records().iloc[1:]).to_reader(),
    ]

    def run(ids: list[str]) -> int:
        result = runner.run(
            "highest",
            inputs={
                "seeds": {"provider": Sources.id, "ids": ids},
                "other": {"provider": Sources.id, "ids": sorted(ids)[:1]},
            },
            interventions=changes,
        )
        return result.tables["data"].value.iloc[0]

    # The target is unused by this metric, but validation reads base membership.
    count = runner.run(
        "seed_count",
        inputs={"seeds": {"provider": Sources.id, "ids": ["s"]}},
        interventions=changes,
    )
    assert count.tables["data"].value.tolist() == [1]
    assert backend.to_pyarrow_batches.call_count == 1
    backend.con.register.assert_not_called()
    assert run([]) == 0
    backend.con.register.assert_not_called()
    assert run(["s", "t"]) == 2**63 - 1
    assert backend.con.register.call_count == 2
    assert run(["t", "s"]) == 2**63 - 1
    assert backend.to_pyarrow_batches.call_count == 3
    assert backend.con.register.call_count == 2
