"""Persisted hybrid graphs retain independent operation and summary context."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime import locations
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from tests.cache_helpers import SCHEMA
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.report_helpers import outcomes
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@dataclass
class HybridState:
    references: tuple[str, ...]
    main_ids: tuple[str, ...] = ("a", "b")
    events: list = field(default_factory=list)
    area_work: list = field(default_factory=list)
    operation_work: list = field(default_factory=list)
    consumer_work: list = field(default_factory=list)
    summary_work: list = field(default_factory=list)

    def clear(self) -> None:
        for calls in (
            self.events,
            self.area_work,
            self.operation_work,
            self.consumer_work,
            self.summary_work,
        ):
            calls.clear()

    def request(self, executor: GraphExecutor) -> PreparedExecution:
        return prepare_execution(
            executor.catalog,
            {
                "metric": "comparison",
                "inputs": {
                    "main": {"provider": "target", "ids": list(self.main_ids)},
                    "reference": {"provider": "target", "ids": list(self.references)},
                },
            },
        )


def hybrid_graph(
    tmp_path: Path,
    state: HybridState,
    policy: CachePolicy,
    *,
    explicit: bool,
    derived: bool,
) -> GraphExecutor:
    @metric(
        name="areas",
        description="Areas",
        cache=policy,
        inputs={"main": LocationDependency(provider="target", primary=True)},
        outputs={"data": schema()},
    )
    def areas(main: gpd.GeoDataFrame) -> pd.DataFrame:
        state.area_work.append(set(main.location_id))
        if "source_location_id" in main:
            return pd.DataFrame(
                {
                    "location_id": main.location_id,
                    "row_key": main.source_location_id,
                    "value": 1,
                }
            )
        return pd.DataFrame(
            {
                "location_id": main.location_id,
                "row_key": "area",
                "value": 1 if policy == "per_location" else len(main),
            }
        )

    @location_operation(
        name="neighbors",
        description="Shared reference targets",
        cache=policy,
        inputs={"source": LocationDependency(provider="target")},
        target_provider="target",
    )
    def neighbors(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        state.operation_work.append(set(source.location_id))
        # Both reference groups overlap the primary selection and each other.
        return dict.fromkeys(source.location_id, ("b", "shared"))

    @metric(
        name="summary",
        description="Whole original producer binding",
        cache="full",
        inputs={
            "main": LocationDependency(provider="target"),
            "areas": Dependency("data", producer="areas"),
        },
        outputs={"data": SCHEMA},
    )
    def summary(main: gpd.GeoDataFrame, areas: pd.DataFrame) -> pd.DataFrame:
        state.summary_work.append(set(main.location_id))
        assert set(areas.location_id) == set(state.main_ids)
        return pd.DataFrame({"value": [len(main)]})

    related = Dependency("data", producer="areas")
    if explicit:
        related = related.bind(main=Input("main"))

    @metric(
        name="comparison",
        description="Hybrid comparison",
        cache="per_location",
        inputs={
            "main": LocationDependency(provider="target", primary=True),
            "reference": LocationDependency(provider="target"),
            "area": related,
            "reference_areas": Dependency("data", producer="areas").bind(
                main=LocationDependency(operation="neighbors").bind(
                    source=Input("reference")
                )
                if derived
                else Input("reference")
            ),
            "whole": Dependency("data", producer="summary"),
        },
        outputs={"data": schema()},
    )
    def comparison(
        *,
        main: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        area: pd.DataFrame,
        reference_areas: pd.DataFrame,
        whole: pd.DataFrame,
    ) -> pd.DataFrame:
        state.consumer_work.append(set(main.location_id))
        assert set(area.location_id) == set(main.location_id)
        assert set(reference.location_id) == set(state.references)
        if derived:
            assert Counter(
                zip(reference_areas.row_key, reference_areas.location_id, strict=True)
            ) == Counter(
                (source, target)
                for source in state.references
                for target in ("b", "shared")
            )
        else:
            assert set(reference_areas.location_id) == set(state.references)
        assert whole.value.tolist() == [len(state.main_ids)]
        return area

    definitions = provider_execution_definitions(state.events)
    definitions["metrics"] = PluginDefinition(
        metrics=[areas, summary, comparison], location_operations=[neighbors]
    )
    local = LocalRunner.from_definitions(definitions)
    return GraphExecutor(
        local.catalog,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )


def consumer_keys(result: ExecutionOutcome) -> dict[str | None, str]:
    return dict(
        pair
        for completion in result.completions
        if completion.node == "comparison"
        for pair in completion.computations
    )


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize(
    "mode", [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("references", [("x", "y"), ("a", "b")])
def test_hybrid_partial_hits(
    tmp_path: Path,
    database: Database,
    policy: CachePolicy,
    references: tuple[str, str],
    mode: tuple[bool, bool],
) -> None:
    explicit, derived = mode
    state = HybridState(references)
    executor = hybrid_graph(tmp_path, state, policy, explicit=explicit, derived=derived)
    first = executor.execute(state.request(executor))
    keys = consumer_keys(first)
    # Keep only A's completed comparison; recompute upstreams to exercise a
    # diamond whose summary branch needs the complete original producer binding.
    for records in database.rows.values():
        records[:] = [
            row
            for row in records
            if "value_key" not in row or row["value_key"] == keys["a"]
        ]
    state.clear()
    result = executor.execute(state.request(executor))
    assert_logical_equal(result.outputs["data"], first.outputs["data"])
    outcomes = {(o.node, o.outcome) for o in result.provenance.execution.observations}
    assert ("comparison", "partial_hit") in outcomes
    assert ("comparison", "completed") in outcomes
    assert ("areas", "completed") in outcomes
    assert ("summary", "completed") in outcomes
    assert state.consumer_work == [{"b"}]
    assert state.summary_work == [{"a", "b"}]
    assert {"a", "b"} <= set().union(*state.area_work)
    assert state.operation_work == ([set(references)] if derived else [])
    state.clear()
    executor.execute(state.request(executor))
    assert state.consumer_work == state.area_work == state.summary_work == []
    assert state.operation_work == state.events == []
    check_context_changes(executor, state, policy, keys, derived=derived)


def check_context_changes(
    executor: GraphExecutor,
    state: HybridState,
    policy: CachePolicy,
    keys: dict[str | None, str],
    *,
    derived: bool,
) -> None:
    state.clear()
    state.references = (state.references[0], "z")
    changed = consumer_keys(executor.execute(state.request(executor)))
    assert all(changed[key] != value for key, value in keys.items())
    assert state.consumer_work == [{"a", "b"}]
    assert state.summary_work == []
    if policy == "per_location":
        assert state.area_work == ([{"b", "shared"}] if derived else [{"z"}])
        assert state.operation_work == ([{"z"}] if derived else [])
    state.clear()
    state.main_ids = ("a", "b", "c")
    latest = consumer_keys(executor.execute(state.request(executor)))
    assert all(latest[key] != value for key, value in changed.items())
    assert state.consumer_work == [{"a", "b", "c"}]
    assert state.summary_work == [{"a", "b", "c"}]
    if policy == "per_location":
        assert state.area_work == [{"c"}]
        assert state.operation_work == []


@pytest.mark.parametrize("retain", [False, True])
@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_empty_primary_skips_independent_work(
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    policy: CachePolicy,
    *,
    retain: bool,
) -> None:
    state = HybridState(("x", "y"), main_ids=())
    executor = hybrid_graph(tmp_path, state, policy, explicit=True, derived=True)
    executor.retain_result = retain
    forbidden = Mock(side_effect=AssertionError("Unused reference acquisition"))
    monkeypatch.setattr(locations, "acquire_provider_input", forbidden)
    results = [executor.execute(state.request(executor)) for _ in range(2)]
    forbidden.assert_not_called()
    assert state.events == state.operation_work == state.area_work == []
    assert state.summary_work == state.consumer_work == []
    assert not database.rows.get("stored_results")
    assert not database.rows.get("stored_partitions")
    for result in results:
        assert result.outputs["data"].empty
        owner = result.metadata.outputs["data"].owner_scope
        assert owner is not None
        assert owner.provider_id == "target"
        assert not result.provenance.execution.observations
        if retain:
            assert result.job_id is not None
            with ResultStore(SQLiteStore(tmp_path / "values.sqlite")) as reader:
                restored = reader.read_result(result.job_id)
            assert restored.tables["data"].empty
            assert restored.metadata.outputs == result.metadata.outputs
    assert results[0].job_id != results[1].job_id
    assert all((result.committed_report is not None) == retain for result in results)


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_shared_consumers_and_partial_demand(
    tmp_path: Path, database: Database, policy: CachePolicy
) -> None:
    state = HybridState(("x", "y"))
    runner = hybrid_graph(tmp_path, state, policy, explicit=True, derived=True)
    cold = runner.execute(state.request(runner))
    assert all(
        value == ["executed"]
        for name, value in outcomes(cold).items()
        if name != "areas"
    )
    assert outcomes(cold)["areas"] == ["executed", "executed"]
    keys = consumer_keys(cold)
    for records in database.rows.values():
        records[:] = [
            row
            for row in records
            if "value_key" not in row or row["value_key"] == keys["a"]
        ]
    state.clear()
    partial = runner.execute(state.request(runner))
    assert outcomes(partial)["comparison"] == ["mixed"]
    # The summary consumer needs A/B after another consumer computes B.
    # Reading B from this run's store must not turn that producer into mixed.
    assert outcomes(partial)["areas"] == ["executed", "executed"]
    assert state.consumer_work == [{"b"}]
    state.clear()
    warm = outcomes(runner.execute(state.request(runner)))
    assert warm["comparison"] == ["reused"]
    assert warm["areas"] == ["skipped", "skipped"]
    assert warm["summary"] == ["skipped"]
    assert warm["neighbors"] == ["reused"]
    assert state.operation_work == state.area_work == state.consumer_work == []


def test_zero_partitions_are_skipped_without_fictitious_values(tmp_path: Path) -> None:
    state = HybridState(("x", "y"), main_ids=())
    runner = hybrid_graph(tmp_path, state, "per_location", explicit=True, derived=True)
    result = runner.execute(state.request(runner))
    assert all(
        status == "skipped" for values in outcomes(result).values() for status in values
    )
    assert state.events == state.consumer_work == state.operation_work == []


def test_partition_reuse_and_new_work_share_original_use(tmp_path: Path) -> None:
    state = HybridState(("x", "y"))
    runner = hybrid_graph(tmp_path, state, "per_location", explicit=True, derived=False)
    runner.execute(state.request(runner))
    state.clear()
    state.main_ids = ("a", "b", "d")
    result = runner.execute(state.request(runner))
    assert outcomes(result)["areas"] == ["mixed", "reused"]
    assert state.area_work == [{"d"}]
    report = result.report_state
    root = report.uses[report.root_use_id or ""]
    area_id = dict(root.inputs)["area"]
    assert report.computations[area_id].outcome == "mixed"


@pytest.mark.parametrize("references", [("b",), ("b", "c")])
@pytest.mark.parametrize("partial", [False, True])
def test_distinct_uses_report_overlapping_computation_reuse(
    tmp_path: Path,
    database: Database,
    references: tuple[str, ...],
    *,
    partial: bool,
) -> None:
    state = HybridState(references)
    runner = hybrid_graph(tmp_path, state, "per_location", explicit=True, derived=False)
    if partial:
        initial = runner.execute(state.request(runner))
        keep = consumer_keys(initial)["a"]
        # Leave only the root's A partition. Computing B then serves the distinct
        # reference use through the in-memory completion path.
        for rows in database.rows.values():
            rows[:] = [
                row
                for row in rows
                if "value_key" not in row or row["value_key"] == keep
            ]
        state.clear()
    runner.retain_result = True
    result = runner.execute(state.request(runner))
    assert result.job_id is not None
    with ResultStore(SQLiteStore(tmp_path / "values.sqlite")) as reader:
        restored = reader.read_result(result.job_id)
        report = reader.read_report(restored.metadata.job_id)
    assert restored.report == report
    assert report.job_id == restored.metadata.job_id == result.job_id
    assert_logical_equal(restored.tables["data"], result.outputs["data"])
    assert set(restored.tables["data"].location_id) == {"a", "b"}
    nodes = {node.use_id: node for node in report.nodes}
    root = nodes[report.root_use_id]
    main = nodes[root.inputs["area"].source_use_id]
    reference = nodes[root.inputs["reference_areas"].source_use_id]
    assert main.use_id != reference.use_id
    assert main.outcome == "executed"
    assert reference.outcome == ("mixed" if "c" in references else "reused")
    assert root.outcome == ("mixed" if partial else "executed")
    # Each partition is computed only once despite overlapping consumers.
    assert Counter(key for batch in state.area_work for key in batch) == Counter(
        dict.fromkeys({"a", "b", *references}, 1)
    )
