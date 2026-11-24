"""Native provider execution, routing, and derived-result reuse."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from tests.frame_assertions import assert_logical_equal
from tests.provider_execution_helpers import RESULT, provider_execution_definitions
from tests.sqlite_helpers import Database, database


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize(
    ("name", "inputs"),
    [
        ("count", {"places": {"provider": "target", "ids": ["a", "a"]}}),
        ("count", {"places": {"provider": "source", "ids": ["a"]}}),
        ("derived", {"source": {"provider": "source", "ids": ["a"]}}),
    ],
)
def test_native_local_routes_and_dependency_forwarding(
    name: str, inputs: dict, *, development: bool
) -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    run = runner.run_development if development else runner.run
    assert run(name, inputs=inputs).tables["result"].total.tolist() == [1]
    assert events[-1].startswith("close:")
    assert events.count("metric") == 1


def test_empty_inputs_skip_acquisition() -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(provider_execution_definitions(events))
    assert runner.run(
        "count", inputs={"places": {"provider": "target", "ids": []}}
    ).tables["result"].total.tolist() == [0]
    assert events == ["metric"]


def test_invalid_frame_fails_shared_boundary_and_closes() -> None:
    events: list[str] = []
    runner = LocalRunner.from_definitions(
        provider_execution_definitions(events, invalid=True)
    )
    with pytest.raises(GraphExecutionError, match="schema"):
        runner.run_development(
            "count", inputs={"places": {"provider": "target", "ids": ["a"]}}
        )
    assert events == ["setup:target", "resolve:target", "close:target"]


@pytest.mark.parametrize("forward_reference", [False, True])
def test_nested_derived_inputs_ignore_declaration_order(
    *, forward_reference: bool
) -> None:
    """Nested bindings can use another derived input declared before or after them."""
    providers = provider_execution_definitions([])["providers"]

    @location_operation(
        name="identity",
        code_revision="1",
        description="Keep source records",
        inputs={"records": LocationDependency(provider="source")},
        target_provider="source",
    )
    def identity(records: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        records.attrs["provider_scope"] = {"provider_id": "forged"}
        return {key: (key,) for key in records.location_id}

    derived = [
        ("first", LocationDependency(operation="identity").bind(records=Input("base"))),
        (
            "second",
            LocationDependency(operation="identity").bind(
                records=LocationDependency(operation="identity").bind(
                    records=Input("first")
                )
            ),
        ),
    ]
    if forward_reference:
        derived.reverse()

    @metric(
        name="nested_derived",
        code_revision="1",
        description="Use nested derived records",
        inputs={"base": LocationDependency(provider="source"), **dict(derived)},
        outputs={"result": RESULT},
    )
    def nested_derived(
        base: gpd.GeoDataFrame, first: gpd.GeoDataFrame, second: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        for records in (base, first, second):
            assert records.location_id.tolist() == ["b", "a"]
            assert records.value.tolist() == [1, 1]
            assert str(records.crs) == "EPSG:4326"
        pd.testing.assert_frame_equal(base, first.drop(columns="source_location_id"))
        assert first.source_location_id.tolist() == first.location_id.tolist()
        pd.testing.assert_frame_equal(first, second)
        return pd.DataFrame({"total": [int(second.value.sum())]})

    runner = LocalRunner.from_definitions(
        {
            "providers": providers,
            "calculations": PluginDefinition(
                metrics=[nested_derived], location_operations=[identity]
            ),
        }
    )
    result = runner.run(
        "nested_derived", inputs={"base": {"provider": "source", "ids": ["b", "a"]}}
    ).tables
    assert result["result"].total.tolist() == [2]
    scope = runner.catalog.provider_registry["source"].scope
    bindings = runner.catalog.graph.nodes["nested_derived"].node.location_inputs
    assert all(
        binding.contract.provider_scope == scope for binding in bindings.values()
    )


def test_derived_root_reuses_value_and_summarizes_effective_inputs(
    tmp_path: Path,
    database: Database,
) -> None:
    runner = LocalRunner.from_definitions(provider_execution_definitions([]))
    executor = GraphExecutor(
        runner.catalog,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "derived",
            "inputs": {"source": {"provider": "source", "ids": ["a"]}},
        },
    )
    cold = executor.execute(plan)
    warm = executor.execute(plan)
    completion = next(c for c in warm.completions if c.node == "derived")
    key = completion.computations[0][1]
    assert any(row["value_key"] == key for row in database.rows["stored_results"])
    retained = MetricTables(SQLiteStore(tmp_path / "values.sqlite")).lookup(
        Header(value_key=key, node_id="derived", output_scope="unpartitioned"),
        required=True,
    )
    assert retained is not None
    assert_logical_equal(retained["result"], cold.outputs["result"])
    assert_logical_equal(warm.outputs["result"], cold.outputs["result"])
    assert warm.provenance.execution.native_inputs == ()
    for outcome in (cold,):
        assert {
            (item.name, item.count)
            for item in outcome.provenance.execution.native_inputs
        } == {
            ("source", 1),
            ("places", 1),
        }
    assert any(
        item.node == "derived" and item.outcome == "cache_hit"
        for item in warm.provenance.execution.observations
    )


__all__ = ["database"]
