"""Provider observations follow acquisition boundaries and resolved uses."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.origins import Origin
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import Places
from tests.cache_helpers import SCHEMA
from tests.managed_backend_helpers import managed_backend
from tests.operation_reuse_helpers import WorkloadOptions, configured_workload
from tests.report_helpers import outcomes
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def provider_outcomes(result: ExecutionOutcome) -> dict[str, str]:
    report = result.report_state
    return {use: value.outcome for use, value in report.providers.items()}


@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("target_cache", [False, True])
def test_mapping_hit_still_resolves_targets(
    tmp_path: Path, *, derived: bool, target_cache: bool
) -> None:
    run, events, _, mapping = configured_workload(
        tmp_path, WorkloadOptions(target_cache=target_cache, derived=derived)
    )
    mapping["a"] = ("target",)
    cold = run()
    assert set(provider_outcomes(cold).values()) == {"executed"}
    events.clear()
    warm = run()
    report = warm.report_state
    target = next(use for use, node in report.uses.items() if node.node == "targets")
    assert report.providers[target].outcome == (
        "reused" if target_cache else "executed"
    )
    assert outcomes(warm)["mapping"] == ["reused"]
    assert (("targets", frozenset({"target"})) in events) is not target_cache


def test_downstream_hit_and_empty_requests_skip_acquisition(tmp_path: Path) -> None:
    run, events, _, mapping = configured_workload(
        tmp_path, WorkloadOptions(metric_cache="full")
    )
    mapping["a"] = ("target",)
    run()
    events.clear()
    assert set(provider_outcomes(run()).values()) == {"skipped"}
    assert events == []
    assert set(provider_outcomes(run(())).values()) == {"skipped"}
    assert events == []


@pytest.mark.parametrize("other_provider", [False, True])
def test_selections_and_shared_origins_remain_distinct(
    tmp_path: Path, *, other_provider: bool
) -> None:
    events = []

    class Source(Places):
        id = "source"
        cache = True
        origins = (Origin(id="shared"),)

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append((self.id, set(ids)))
            return super().resolve(ids)

        @override
        def close(self) -> None:
            events.append((self.id, "closed"))

    class Other(Source):
        id = "other"

    second = Other.id if other_provider else Source.id

    @metric(
        name="root",
        description="Consume distinct selections",
        outputs={"data": SCHEMA},
        inputs={
            "left": LocationDependency(provider=Source.id),
            "right": LocationDependency(provider=second),
        },
    )
    def root(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(left) + len(right)]})

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[root], resources={"source": Source, "other": Other}
            )
        }
    )
    store = SQLiteStore(tmp_path / "records.sqlite")
    executor = GraphExecutor(
        runner.catalog,
        Mock(),
        store=store,
        provider_records=ProviderRecords(store),
    )

    def run(left: list[str], right: list[str]) -> ExecutionOutcome:
        return executor.execute(
            prepare_execution(
                runner.catalog,
                {
                    "metric": "root",
                    "inputs": {
                        "left": {"provider": Source.id, "ids": left},
                        "right": {"provider": second, "ids": right},
                    },
                },
            )
        )

    cold = run(["a"], ["a", "b"])
    report = cold.report_state
    assert report.root_use_id is not None
    links = dict(report.uses[report.root_use_id].inputs)
    assert links["left"] != links["right"]
    assert report.providers[links["left"]].outcome == "executed"
    assert report.providers[links["right"]].outcome == (
        "executed" if other_provider else "mixed"
    )
    assert (Source.id, "closed") in events
    assert (
        report.descriptions[links["left"]].origins
        == report.descriptions[links["right"]].origins
    )
    events.clear()
    assert set(provider_outcomes(run(["a"], ["b"])).values()) == {"reused"}
    assert events == []
    mixed = run(["a", "c"], [])
    assert mixed.report_state.root_use_id is not None
    links = dict(mixed.report_state.uses[mixed.report_state.root_use_id].inputs)
    assert mixed.report_state.providers[links["left"]].outcome == "mixed"
    assert mixed.report_state.providers[links["right"]].outcome == "skipped"

    shared = run(["fresh"], ["fresh"])
    assert set(provider_outcomes(shared).values()) == {"executed"}
    assert len(shared.report_state.providers) == (2 if other_provider else 1)


@pytest.mark.parametrize("adapter", ["parquet", "postgres"])
@pytest.mark.parametrize("matches", [False, True])
def test_queries_execute_even_with_cached_overlapping_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: str, *, matches: bool
) -> None:
    native, resources, config = managed_backend(adapter, tmp_path, monkeypatch)
    if not matches:
        original = native.to_pyarrow_batches.side_effect
        native.to_pyarrow_batches.side_effect = lambda *_a, **_kw: (
            original().read_all().slice(0, 0).to_reader()
        )

    def selector(source: gpd.GeoDataFrame, target: ir.Table) -> ir.Table:
        assert not source.empty
        return target

    @location_operation(
        name="choose",
        description="Select targets",
        cache="off",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider="targets",
        target_selector=selector,
    )
    def choose(source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> dict:
        return {key: tuple(candidates.location_id) for key in source.location_id}

    @metric(
        name="root",
        description="Consume targets",
        outputs={"data": SCHEMA},
        inputs={
            "source": LocationDependency(provider=Places.id),
            "selected": LocationDependency(operation="choose").bind(
                source=Input("source")
            ),
        },
    )
    def root(source: gpd.GeoDataFrame, selected: gpd.GeoDataFrame) -> pd.DataFrame:
        assert not source.empty
        return pd.DataFrame({"value": [len(selected)]})

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[root],
                location_operations=[choose],
                resources={"source": Places, **resources},
            )
        }
    )
    store = SQLiteStore(tmp_path / "records.sqlite")
    executor = GraphExecutor(
        runner.catalog,
        Mock(),
        resource_config=config,
        store=store,
        provider_records=ProviderRecords(store),
        retained_providers=frozenset({"targets"}),
    )
    plan = prepare_execution(
        runner.catalog,
        {"metric": "root", "inputs": {"source": {"provider": Places.id, "ids": ["a"]}}},
    )
    for _ in range(2):
        report = executor.execute(plan).report_state
        operation = next(use for use in report.uses.values() if use.node == "choose")
        assert operation.target_use_id is not None
        assert report.providers[operation.target_use_id].outcome == "executed"
        assert report.target_queries == {operation.target_use_id}
        description = report.descriptions[operation.use_id].operation
        assert description is not None
        assert description.target_adapter == (
            "postgis" if adapter == "postgres" else adapter
        )
        assert description.target_scope.provider_id == "targets"
    assert native.to_pyarrow_batches.call_count == 2
    assert native.disconnect.call_count == 2
