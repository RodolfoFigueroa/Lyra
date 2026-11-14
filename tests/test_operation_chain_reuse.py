"""Correspondence membership and producer scope survive chained cache lookup."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import Parameters, Places
from tests.cache_helpers import SCHEMA
from tests.metric_execution_helpers import schema
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def chain(
    tmp_path: Path,
    policy: CachePolicy,
    *,
    mode: str = "canonical",
    revision: str = "1",
) -> tuple[Callable[..., ExecutionOutcome], list, list]:
    explicit = mode == "explicit"
    events = []
    seen = []

    class Source(Places):
        id = "sources"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(("sources", frozenset(ids)))
            return super().resolve(ids)

    class Middle(Source):
        id = "middle"

    class Target(Source):
        id = "targets"

    @location_operation(
        name="first",
        description="First hop",
        canonical=True,
        cache=policy,
        code_revision=revision,
        inputs={"source": LocationDependency(provider="sources")},
        target_provider="middle",
    )
    def first(source: gpd.GeoDataFrame, *, parameters: Parameters) -> dict:
        assert parameters.count >= 0
        events.append(("first", frozenset(source.location_id)))
        return {
            key: (() if mode == "empty" else (key.upper(),))
            for key in source.location_id
        }

    @location_operation(
        name="second",
        description="Second hop",
        canonical=True,
        cache=policy,
        inputs={"source": LocationDependency(provider="middle")},
        target_provider="targets",
    )
    def second(source: gpd.GeoDataFrame, *, parameters: Parameters) -> dict:
        assert parameters.count >= 0
        events.append(("second", frozenset(source.location_id)))
        return {
            key: ("shared" if key in {"A", "B"} else key.lower(),)
            for key in source.location_id
        }

    inputs = {"places": LocationDependency(provider="targets", primary=True)}
    if explicit:
        inputs = {
            "source": LocationDependency(provider="sources"),
            "places": LocationDependency(operation="second", primary=True).bind(
                source=LocationDependency(
                    operation="first", parameters={"count": Input("parameters.count")}
                ).bind(source=Input("source"))
            ),
        }

    def consume(places: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(
            set(zip(places.source_location_id, places.location_id, strict=True))
        )
        events.append(("consumer", frozenset(places.location_id)))
        ids = sorted(set(places.location_id))
        return pd.DataFrame(
            {
                "location_id": ids,
                "row_key": ["value"] * len(ids),
                "value": pd.array([1] * len(ids), dtype="Int64"),
            }
        )

    if explicit:

        def handler(
            places: gpd.GeoDataFrame, source: gpd.GeoDataFrame, parameters: Parameters
        ) -> pd.DataFrame:
            assert parameters.count >= 0
            assert set(source.location_id).issubset({"a", "b", "c"})
            return consume(places)
    else:

        def handler(places: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
            assert parameters.count >= 0
            return consume(places)

    consumer = metric(
        name="consumer",
        description="Consume chain",
        cache="per_location",
        inputs=inputs,
        outputs={"data": schema()},
    )(handler)

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[consumer, branch_metric(events)]
                if mode == "branch"
                else [consumer],
                location_operations=[first, second],
                resources={"source": Source, "middle": Middle, "target": Target},
            )
        }
    )
    executor = GraphExecutor(
        runner.catalog,
        runner.catalog.implementations,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )

    def run(ids: tuple[str, ...] = ("a", "b", "c"), count: int = 2) -> ExecutionOutcome:
        return executor.execute(
            prepare_execution(
                runner.catalog,
                {
                    "metric": "outer" if mode == "branch" else "consumer",
                    "parameters": {"count": count},
                    "inputs": {
                        "source" if explicit else "places": {
                            "provider": "sources",
                            "ids": list(ids),
                        }
                    },
                },
            )
        )

    return run, events, seen


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("explicit", [False, True])
def test_chain_hits_and_partial_consumer_reuse(
    tmp_path: Path, database: Database, policy: CachePolicy, *, explicit: bool
) -> None:
    run, events, seen = chain(
        tmp_path, policy, mode="explicit" if explicit else "canonical"
    )
    first = run()
    assert set(first.outputs["data"].location_id) == {"shared", "c"}
    assert seen == [{("A", "shared"), ("B", "shared"), ("C", "c")}]
    events.clear()
    run()
    assert events == []
    # Evict one complete downstream partition, leaving all upstream mappings.
    key = next(
        key
        for item in first.completions
        if item.node == "consumer"
        for group, key in item.computations
        if group == "c"
    )
    database.rows["stored_results"][:] = [
        row for row in database.rows["stored_results"] if row["value_key"] != key
    ]
    run()
    assert ("consumer", frozenset({"c"})) in events
    assert seen[-1] == {("C", "c")}
    if policy == "off":
        assert ("first", frozenset({"a", "b", "c"})) in events
        assert ("second", frozenset({"A", "B", "C"})) in events
    else:
        assert not any(name in {"first", "second"} for name, _ in events)


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_revision_invalidates_equal_targets(
    tmp_path: Path, policy: CachePolicy
) -> None:
    chain(tmp_path, policy)[0]()
    run, events, _ = chain(tmp_path, policy, revision="2")
    run()
    assert ("consumer", frozenset({"shared", "c"})) in events


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_parameters_invalidate_equal_targets(
    tmp_path: Path, policy: CachePolicy
) -> None:
    run, events, _ = chain(tmp_path, policy, mode="explicit")
    run()
    events.clear()
    run(count=3)
    assert ("first", frozenset({"a", "b", "c"})) in events
    assert ("consumer", frozenset({"shared", "c"})) in events


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_empty_chain_retains_target_scope(tmp_path: Path, policy: CachePolicy) -> None:
    run, events, _ = chain(tmp_path, policy, mode="empty")
    result = run()
    assert result.outputs["data"].empty
    owner = result.metadata.outputs["data"].owner_scope
    assert owner is not None
    assert owner.provider_id == "targets"
    events.clear()
    assert run().outputs["data"].empty
    assert events == []


def test_repeated_target_identity_retains_all_sources(tmp_path: Path) -> None:
    run, events, seen = chain(tmp_path, "per_location")
    run(("a",))
    events.clear()
    run(("a", "b"))
    assert ("consumer", frozenset({"shared"})) in events
    assert seen[-1] == {("A", "shared"), ("B", "shared")}
    events.clear()
    run(("b", "a"))
    assert events == []


def test_overlapping_chain_requests_compute_only_missing_source_groups(
    tmp_path: Path,
) -> None:
    run, events, seen = chain(tmp_path, "per_location")
    run(("a", "b"))
    events.clear()
    run(("b", "c"))
    assert ("first", frozenset({"c"})) in events
    assert ("second", frozenset({"C"})) in events
    assert seen[-1] == {("B", "shared"), ("C", "c")}
    assert not any(name == "first" and "b" in ids for name, ids in events)


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_separate_branch_still_acquires_required_sources(
    tmp_path: Path, policy: CachePolicy
) -> None:
    run, events, _ = chain(tmp_path, policy, mode="branch")
    run()
    events.clear()
    run()
    assert ("sources", frozenset({"a", "b", "c"})) in events
    assert ("outer", frozenset({"a", "b", "c"})) in events
    assert not any(name in {"first", "second", "consumer"} for name, _ in events)


def branch_metric(events: list) -> Callable:
    @metric(
        name="outer",
        description="Independent work alongside a cached chain",
        inputs={
            "places": LocationDependency(provider="sources"),
            "cached": Dependency("data", producer="consumer"),
        },
        outputs={"data": SCHEMA},
    )
    def outer(
        places: gpd.GeoDataFrame, cached: pd.DataFrame, parameters: Parameters
    ) -> pd.DataFrame:
        assert parameters.count >= 0
        events.append(("outer", frozenset(places.location_id)))
        assert set(cached.location_id) == {"shared", "c"}
        return pd.DataFrame({"value": [len(places)]})

    return outer
