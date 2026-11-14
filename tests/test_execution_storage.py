"""Current native values retain execution contracts across storage boundaries."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field
from shapely.geometry import Point

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    MetricParameters,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.storage_schema import empty_frame
from tests.sqlite_helpers import Database, database

__all__ = ["database"]

from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RESULT, provider_execution_definitions


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_owned_tables_and_captured_completion(
    database: Database, tmp_path: Path, *, spatial: bool, empty: bool
) -> None:
    declaration = schema(spatial=spatial)
    calls = []

    @metric(
        name="findings",
        description="Findings",
        code_revision="1",
        cache="full",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"a": declaration, "b": declaration},
    )
    def findings(places: gpd.GeoDataFrame) -> dict:
        assert set(places.location_id) == {"a", "b"}
        calls.append(True)
        frame = (
            empty_frame(declaration)
            if empty
            else pd.DataFrame(
                {
                    "location_id": ["a"],
                    "row_key": ["one"],
                    "value": pd.array([None], dtype="Int64"),
                }
            )
        )
        if spatial and not empty:
            frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0)], crs=4326)
        return {"a": frame, "b": frame}

    definitions = provider_execution_definitions([])
    definitions["findings"] = PluginDefinition(metrics=[findings])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "results.sqlite3"
    )
    inputs = {"places": {"provider": "target", "ids": ["a", "b"]}}
    result = runner.run("findings", inputs=inputs)
    again = runner.run("findings", inputs=inputs)
    assert calls == [True]
    store = SQLiteStore(tmp_path / "values.sqlite")
    with store.engine.connect() as connection:
        saved_header = store.header(
            connection, database.rows["stored_results"][0]["value_key"]
        )
    assert saved_header.owner_scope == result.metadata.outputs["a"].owner_scope
    assert {row["location_id"] for row in database.rows["stored_partitions"]} == {
        "a",
        "b",
    }
    header = saved_header.model_copy(update={"output_rows": None})
    tables = MetricTables(store)
    result.metadata.outputs["a"].table_schema.columns.clear()
    result.tables["a"]["value"] = 99
    restored = tables.lookup(header, required=True)
    assert restored is not None
    pd.testing.assert_frame_equal(restored["a"], again.tables["a"])
    restored["a"]["value"] = 13
    reread = tables.lookup(header, required=True)
    assert reread is not None
    pd.testing.assert_frame_equal(reread["a"], again.tables["a"])
    node = runner.catalog.graph.nodes["findings"].node
    assert node.kind == "metric"
    node.outputs["a"].columns[0].description = "Changed catalog"
    registrations = store.load_registrations()
    assert registrations[0].columns[0].description != "Changed catalog"


@pytest.mark.parametrize("matches", [False, True])
def test_operation_mapping_reuse_resolves_targets(
    database: Database, tmp_path: Path, *, matches: bool
) -> None:
    events = []
    definitions = provider_execution_definitions(events)
    del definitions["calculations"]

    @location_operation(
        name="match",
        description="Match",
        code_revision="1",
        cache="full",
        inputs={"places": LocationDependency(provider="source")},
        target_provider="target",
    )
    def match(places: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        assert set(places.location_id) == {"a", "b", "c"}
        events.append("match")
        return {"a": ("shared",), "b": ("shared",)} if matches else {}

    @metric(
        name="count",
        description="Count",
        code_revision="1",
        outputs={"count": RESULT},
        inputs={
            "source": LocationDependency(provider="source"),
            "places": LocationDependency(operation="match").bind(
                places=Input("source")
            ),
        },
    )
    def count(source: gpd.GeoDataFrame, places: gpd.GeoDataFrame) -> pd.DataFrame:
        assert set(source.location_id) == {"a", "b", "c"}
        assert places.location_id.tolist() == (["shared", "shared"] if matches else [])
        return pd.DataFrame({"total": [len(places)]})

    definitions["matches"] = PluginDefinition(
        metrics=[count], location_operations=[match]
    )
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "results.sqlite3"
    )
    for _ in range(2):
        runner.run(
            "count", inputs={"source": {"provider": "source", "ids": ["a", "b", "c"]}}
        )
    assert events.count("match") == 1
    assert events.count("resolve:target") == (2 if matches else 0)
    assert events.count("setup:target") == (2 if matches else 0)
    store = SQLiteStore(tmp_path / "values.sqlite")
    key = database.rows["stored_results"][0]["value_key"]
    with store.engine.connect() as connection:
        header = store.header(connection, key)
    saved = OperationPairs(store).lookup(header, ("a", "b", "c"))
    assert saved == {
        "a": ("shared",) if matches else (),
        "b": ("shared",) if matches else (),
        "c": (),
    }

    if matches:
        registration = next(
            r for r in store.load_registrations() if r.kind == "operation"
        )
        database.rows[store.layout.tables[registration.key].table.name].pop()
        before = events.count("resolve:target")
        with pytest.raises(GraphExecutionError) as captured:
            runner.run(
                "count",
                inputs={"source": {"provider": "source", "ids": ["a", "b", "c"]}},
            )
        assert isinstance(captured.value.__cause__, DamagedDataError)
        assert events.count("match") == 1
        assert events.count("resolve:target") == before


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("cache_policy", ["off", "full"])
def test_cache_policy_and_trusted_write_validation(
    tmp_path: Path, cache_policy: CachePolicy
) -> None:
    checks = []
    calls = []

    def check_values(values: pd.Series) -> bool:
        checks.append(True)
        return bool((values >= 0).all())

    declaration = RESULT.update_column(
        "total", checks=pa.Check(check_values, name="nonnegative")
    )

    @metric(
        name="total",
        description="Total",
        code_revision="1",
        outputs={"total": declaration},
        cache=cache_policy,
    )
    def total() -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame({"total": [1]})

    runner = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[total])},
        result_store_path=tmp_path / "results.sqlite3",
    )
    runner.run("total", inputs={})
    assert checks == [True]  # A trusted write must not rerun producer checks.
    runner.run("total", inputs={})
    assert len(calls) == (1 if cache_policy == "full" else 2)
    assert len(checks) == 2  # Restored values have an independent validation boundary.


@pytest.mark.parametrize("policy", ["off", "full"])
@pytest.mark.parametrize("empty", [False, True])
def test_unpartitioned_terminal_retention(
    database: Database, tmp_path: Path, policy: CachePolicy, *, empty: bool
) -> None:
    calls = []

    @metric(
        name="terminal", description="Terminal", outputs={"total": RESULT}, cache=policy
    )
    def terminal() -> pd.DataFrame:
        calls.append(True)
        return empty_frame(RESULT) if empty else pd.DataFrame({"total": [1, 1]})

    local = LocalRunner.from_definitions({"p": PluginDefinition(metrics=[terminal])})
    executor = GraphExecutor(
        local.catalog,
        local.catalog.implementations,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
        retain_result=True,
    )
    invocation = local.catalog.implementations["p"].prepare_resolved_invocation(
        "terminal", {}
    )
    first = executor.execute(invocation)
    second = executor.execute(invocation)
    assert first.result_ref is not None
    assert second.result_ref is not None
    assert first.result_ref != second.result_ref
    description, frames = ResultDescriptions(
        SQLiteStore(tmp_path / "values.sqlite")
    ).read(first.result_ref)
    assert description.metric_name == "terminal"
    pd.testing.assert_frame_equal(
        frames["total"], first.outputs["total"].astype({"total": "Int64"})
    )
    assert len(calls) == (2 if policy == "off" else 1)
    assert len(database.rows["stored_results"]) == 1
    key = first.completions[0].computations[0][1]
    saved = MetricTables(SQLiteStore(tmp_path / "values.sqlite")).lookup(
        Header(value_key=key, node_id="terminal", output_scope="unpartitioned"),
        required=True,
    )
    assert saved is not None
    assert saved["total"].total.tolist() == ([] if empty else [1, 1])
    assert not database.rows.get("stored_partitions")

    @metric(
        name="terminal", description="Terminal", outputs={"total": RESULT}, cache="off"
    )
    def terminal_off() -> pd.DataFrame:
        return terminal()

    uncached = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[terminal_off])}
    )
    uncached_executor = GraphExecutor(
        uncached.catalog,
        uncached.catalog.implementations,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
        retain_result=True,
    )
    third = uncached_executor.execute(
        uncached.catalog.implementations["p"].prepare_resolved_invocation(
            "terminal", {}
        )
    )
    assert third.result_ref not in {first.result_ref, second.result_ref}
    assert len(database.rows["stored_results"]) == 1
    assert len(database.rows["final_results"]) == 3
    assert (
        ResultDescriptions(SQLiteStore(tmp_path / "values.sqlite")).read(
            first.result_ref
        )[0]
        == description
    )


@pytest.mark.parametrize("fault", ["missing", "extra", "invalid"])
def test_invalid_unpartitioned_outputs_never_publish(
    database: Database, tmp_path: Path, fault: str
) -> None:
    @metric(
        name="invalid",
        description="Invalid",
        outputs={"a": RESULT, "b": RESULT},
        cache="full",
    )
    def invalid() -> dict:
        frame = pd.DataFrame({"total": [1]})
        return (
            {"a": frame}
            if fault == "missing"
            else {"a": frame, "b": frame, "c": frame}
            if fault == "extra"
            else {"a": frame, "b": pd.DataFrame({"wrong": [1]})}
        )

    runner = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[invalid])},
        result_store_path=tmp_path / "results.sqlite3",
    )
    with pytest.raises(GraphExecutionError):
        runner.run("invalid", inputs={})
    assert not database.rows.get("stored_results")


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_scoped_computation_publication(
    database: Database,
    tmp_path: Path,
    policy: CachePolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    execute = GraphExecutor.execute
    references = []

    def retain(
        executor: GraphExecutor, root: PreparedExecution | PreparedResolvedInvocation
    ) -> object:
        executor.retain_result = True
        outcome = execute(executor, root)
        assert outcome.result_ref is not None
        references.append(outcome.result_ref)
        description, tables = ResultDescriptions(
            SQLiteStore(tmp_path / "values.sqlite")
        ).read(outcome.result_ref)
        assert description.metric_name == "scoped"
        assert (
            tables["a"].location_id.tolist()
            == outcome.outputs["a"].location_id.tolist()
        )
        assert tables["b"].value.tolist() == outcome.outputs["b"].value.tolist()
        return outcome

    monkeypatch.setattr(GraphExecutor, "execute", retain)

    @metric(
        name="scoped",
        description="Scoped",
        cache=policy,
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"a": schema(), "b": schema()},
    )
    def scoped(places: gpd.GeoDataFrame) -> dict:
        calls.append(tuple(places.location_id))
        frame = pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": "same",
                "value": pd.array([1] * len(places), dtype="Int64"),
            }
        )
        return {"a": frame, "b": frame}

    definitions = provider_execution_definitions([])
    definitions["scoped"] = PluginDefinition(metrics=[scoped])
    local = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "results.sqlite3"
    )

    def run(ids: list[str]) -> None:
        local.run("scoped", inputs={"places": {"provider": "target", "ids": ids}})

    run(["a", "b"])
    first = {row["value_key"] for row in database.rows.get("stored_results", [])}
    run(["a", "c"])
    run(["a"])
    run(["a", "b"])
    assert len(calls) == (
        2 if policy == "per_location" else 3 if policy == "full" else 4
    )
    if policy == "per_location":
        assert len(database.rows["stored_results"]) == 3
        assert len(first) == 2
    run([])
    assert len(set(references)) == 5
    if policy == "full":
        assert len(database.rows["stored_results"]) == 4
    elif policy == "per_location":
        assert len(database.rows["stored_results"]) == 3


class ContextParameters(MetricParameters):
    """Output-affecting context offset."""

    offset: int = Field(0, description="Offset")


@pytest.mark.parametrize("policy", ["full", "per_location"])
def test_scoped_shared_context_and_parameters(
    database: Database, tmp_path: Path, policy: CachePolicy
) -> None:
    calls = []

    @metric(
        name="contextual",
        description="Contextual",
        cache=policy,
        inputs={
            "places": LocationDependency(provider="target", primary=True),
            "reference": LocationDependency(provider="target"),
        },
        outputs={"a": schema()},
    )
    def contextual(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        parameters: ContextParameters,
    ) -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": "same",
                "value": pd.array(
                    [len(reference) + parameters.offset] * len(places), dtype="Int64"
                ),
            }
        )

    definitions = provider_execution_definitions([])
    definitions["contextual"] = PluginDefinition(metrics=[contextual])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "results.sqlite3"
    )
    for _ in range(2):
        for references, offset, expected in (
            (["b"], 0, 1),
            (["b", "c"], 0, 2),
            (["b"], 5, 6),
        ):
            result = runner.run(
                "contextual",
                inputs={
                    "places": {"provider": "target", "ids": ["a"]},
                    "reference": {"provider": "target", "ids": references},
                },
                parameters={"offset": offset},
            )
            assert result.tables["a"].value.tolist() == [expected]
    assert len(calls) == 3
    assert len(database.rows["stored_results"]) == 3
