"""Operation stages describe actual work independently of mapping reuse."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.reports import ExecutionReport, ReportNode
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import Input, LocationDependency, PluginDefinition, metric
from tests.cache_helpers import SCHEMA
from tests.operation_reuse_helpers import workload as mapping_workload
from tests.selector_reuse_helpers import selector_definitions, workload
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def operation_node(result: ExecutionOutcome, name: str = "mapping") -> ReportNode:
    nodes = result.report_state.nodes
    assert nodes is not None
    return next(node for node in nodes if node.node_id == name)


def stages(node: ReportNode) -> tuple[str, str, str]:
    assert node.operation is not None
    return (
        node.operation.selector,
        node.operation.target_query,
        node.operation.mapping,
    )


@pytest.mark.parametrize("policy", ["full", "per_location"])
@pytest.mark.parametrize("target_cache", [False, True])
@pytest.mark.parametrize("ids", [("a", "b"), ("empty",), ()])
def test_selector_stages_and_mapping_reuse(
    tmp_path: Path, policy: CachePolicy, ids: tuple[str, ...], *, target_cache: bool
) -> None:
    run, events, _ = workload(tmp_path, policy=policy, target_cache=target_cache)
    cold = operation_node(run(ids))
    assert stages(cold) == (("executed",) * 3 if ids else ("skipped",) * 3)
    events.clear()
    warm = run(ids)
    node = operation_node(warm)
    assert stages(node) == ("skipped",) * 3
    assert node.outcome == ("reused" if ids or policy == "full" else "skipped")
    assert not any(e[0] in {"selector", "query", "mapping"} for e in events)
    if ids == ("a", "b") and not target_cache:
        assert ("exact", frozenset({"a-2", "b-2"})) in events
        assert any(
            n.kind == "provider" and n.node_id == "targets" and n.outcome == "executed"
            for n in warm.report_state.nodes or ()
        )


def test_partial_reuse_runs_all_stages(tmp_path: Path) -> None:
    run, _, _ = workload(tmp_path)
    run()
    node = operation_node(run(("b", "c")))
    assert node.outcome == "mixed"
    assert stages(node) == ("executed",) * 3


@pytest.mark.parametrize("derived", [False, True])
def test_mapping_without_selector(tmp_path: Path, *, derived: bool) -> None:
    run, _, _, _ = mapping_workload(tmp_path, derived=derived)
    assert stages(operation_node(run())) == (
        "not_applicable",
        "not_applicable",
        "executed",
    )
    assert stages(operation_node(run())) == (
        "not_applicable",
        "not_applicable",
        "skipped",
    )
    assert stages(operation_node(run(()))) == (
        "not_applicable",
        "not_applicable",
        "skipped",
    )


def test_downstream_hit_keeps_target_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definitions, events, _ = selector_definitions(metric_cache="full")
    runner = LocalRunner.from_definitions(definitions)
    store = SQLiteStore(tmp_path / "values.sqlite")
    executor = GraphExecutor(
        runner.catalog,
        Mock(),
        store=store,
        provider_records=ProviderRecords(store),
    )
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "consumer",
            "inputs": {"source": {"provider": "sources", "ids": ["a", "b"]}},
        },
    )
    cold = operation_node(executor.execute(plan))
    blocked = Mock(side_effect=AssertionError("target initialized on a hit"))
    target = definitions["test"].resources["target"]
    monkeypatch.setattr(target, "__init__", blocked)
    monkeypatch.setattr(target, "setup", blocked)
    events.clear()
    warm = operation_node(executor.execute(plan))
    blocked.assert_not_called()
    assert events == []
    assert stages(warm) == ("skipped",) * 3
    assert warm.outcome == "reused"
    assert cold.operation is not None
    assert warm.operation is not None
    assert warm.operation.target_scope == cold.operation.target_scope
    assert warm.operation.target_adapter == cold.operation.target_adapter


def test_shared_operation_and_finalized_data_are_complete_and_detached() -> None:
    definitions, events, _ = selector_definitions()
    binding = LocationDependency(operation="mapping").bind(source=Input("source"))

    @metric(
        name="shared",
        description="Consume one operation twice",
        outputs={"data": SCHEMA},
        inputs={
            "source": LocationDependency(provider="sources"),
            "left": binding,
            "right": binding,
        },
    )
    def shared(
        source: gpd.GeoDataFrame, left: gpd.GeoDataFrame, right: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        assert len(source) == 2
        assert left.equals(right)
        return pd.DataFrame({"value": [len(left)]})

    definitions["shared"] = PluginDefinition(metrics=[shared])
    runner = LocalRunner.from_definitions(definitions)
    catalog = runner.catalog
    plan = prepare_execution(
        catalog,
        {
            "metric": "shared",
            "inputs": {"source": {"provider": "sources", "ids": ["a", "b"]}},
        },
    )
    executor = GraphExecutor(catalog, Mock())
    result = executor.execute(plan)
    state = result.report_state
    assert state.nodes is not None
    operations = [node for node in state.nodes if node.kind == "operation"]
    assert len(operations) == 1
    assert stages(operations[0]) == ("executed",) * 3
    assert ("mapping", frozenset({"a", "b"})) in events
    assert state.request is not None
    assert state.root_use_id is not None
    # Publication only needs to bind a final result reference to this complete data.
    report = ExecutionReport(
        request=state.request,
        job_id="a" * 32,
        status="succeeded",
        root_use_id=state.root_use_id,
        nodes=list(state.nodes),
        parameter_bindings=state.parameter_bindings,
        build_revision=state.build_revision,
    )
    operation = operations[0]
    state.descriptions[operation.use_id].parameters["count"] = 99
    assert operation.parameters["count"] == 2
    operation.parameters["count"] = 100
    assert (
        next(n for n in report.nodes if n.use_id == operation.use_id).parameters[
            "count"
        ]
        == 2
    )


@pytest.mark.parametrize("right_ids", [["b"], ["b", "c"]])
def test_distinct_operations_report_overlapping_mapping_reuse(
    tmp_path: Path, right_ids: list[str]
) -> None:
    definitions, events, _ = selector_definitions()

    @metric(
        name="comparison",
        description="Compare separately selected targets",
        inputs={
            "left": LocationDependency(provider="sources"),
            "right": LocationDependency(provider="sources"),
            "left_targets": LocationDependency(operation="mapping").bind(
                source=Input("left")
            ),
            "right_targets": LocationDependency(operation="mapping").bind(
                source=Input("right")
            ),
        },
        outputs={"data": SCHEMA},
    )
    def comparison(
        left: gpd.GeoDataFrame,
        right: gpd.GeoDataFrame,
        left_targets: gpd.GeoDataFrame,
        right_targets: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert set(left_targets.source_location_id) == set(left.location_id)
        assert set(right_targets.source_location_id) == set(right.location_id)
        return pd.DataFrame({"value": [len(left) + len(right)]})

    definitions["comparison"] = PluginDefinition(metrics=[comparison])
    runner = LocalRunner.from_definitions(
        definitions, result_store_path=tmp_path / "values.sqlite"
    )
    result = runner.run(
        "comparison",
        inputs={
            "left": {"provider": "sources", "ids": ["a", "b"]},
            "right": {"provider": "sources", "ids": right_ids},
        },
    )
    nodes = {node.use_id: node for node in result.report.nodes}
    root = nodes[result.report.root_use_id]
    left = nodes[root.inputs["left_targets"].source_use_id]
    right = nodes[root.inputs["right_targets"].source_use_id]
    assert left.use_id != right.use_id
    assert left.outcome == "executed"
    assert right.outcome == ("mixed" if "c" in right_ids else "reused")
    assert stages(right) == (
        ("executed",) * 3 if "c" in right_ids else ("skipped",) * 3
    )
    expected = [frozenset({"a", "b"})] + (
        [frozenset({"c"})] if "c" in right_ids else []
    )
    for stage in ("selector", "mapping"):
        assert [event[1] for event in events if event[0] == stage] == expected
    assert [event[1] for event in events if event[0] == "query"] == [
        frozenset(f"{key}-2" for key in batch) for batch in expected
    ]
    result.metadata.store_path.touch()
    with ResultStore.open(result.metadata.store_path) as reader:
        assert reader.read_report(result.metadata.job_id) == result.report
        assert reader.read_result(result.metadata.job_id).report == result.report
