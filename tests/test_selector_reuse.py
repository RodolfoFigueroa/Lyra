"""Selector lookup controls query membership and mapping as one computation."""

from dataclasses import replace
from pathlib import Path

import pytest

from lyra.contracts.metric_scope import CachePolicy
from tests.selector_reuse_helpers import Identity, workload
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def callbacks(events: list) -> list:
    return [event for event in events if event[0] in {"selector", "query", "mapping"}]


@pytest.mark.parametrize("target_cache", [False, True])
@pytest.mark.parametrize("policy", ["full", "per_location"])
@pytest.mark.parametrize("ids", [("a", "b"), ("empty",), ()])
def test_complete_hits_skip_selection(
    tmp_path: Path, *, target_cache: bool, policy: CachePolicy, ids: tuple[str, ...]
) -> None:
    run, events, seen = workload(tmp_path, policy=policy, target_cache=target_cache)
    run(ids)
    assert bool(callbacks(events)) == bool(ids)
    assert not any(event[0] == "exact" for event in events)
    expected = seen[-1]
    events.clear()
    run(tuple(reversed(ids)))
    assert callbacks(events) == []
    assert seen[-1] == expected
    assert [event for event in events if event[0] == "exact"] == (
        [("exact", frozenset({"a-2", "b-2"}))]
        if not target_cache and ids == ("a", "b")
        else []
    )


@pytest.mark.parametrize("target_cache", [False, True])
def test_partial_hit_selects_missing_sources_and_keeps_retained_groups(
    tmp_path: Path, *, target_cache: bool
) -> None:
    run, events, seen = workload(tmp_path, target_cache=target_cache)
    run()
    events.clear()
    run(("b", "c"))
    assert callbacks(events) == [
        ("selector", frozenset({"c"})),
        ("query", frozenset({"c-2"})),
        ("mapping", frozenset({"c"})),
    ]
    assert seen[-1] == {("b", "b-2"), ("c", "c-2")}
    assert [event for event in events if event[0] == "exact"] == (
        [] if target_cache else [("exact", frozenset({"b-2"}))]
    )


@pytest.mark.parametrize("policy", ["off", "full"])
def test_miss_queries_complete_batch_even_with_retained_records(
    tmp_path: Path, policy: CachePolicy
) -> None:
    run, events, seen = workload(tmp_path, policy=policy)
    run()
    events.clear()
    # A full miss caused by a smaller binding still queries cached target A.
    run(("a",))
    assert callbacks(events) == [
        ("selector", frozenset({"a"})),
        ("query", frozenset({"a-2"})),
        ("mapping", frozenset({"a"})),
    ]
    assert seen[-1] == {("a", "a-2")}


@pytest.mark.parametrize("target_cache", [False, True])
def test_downstream_hit_bypasses_all_acquisition(
    tmp_path: Path, *, target_cache: bool
) -> None:
    run, events, _ = workload(tmp_path, metric_cache="full", target_cache=target_cache)
    run()
    events.clear()
    run(parameters={"metric_count": 2})
    assert events == []
    run(parameters={"metric_count": 3})
    # Mapping uses its own defaults; changing only metric parameters leaves it valid.
    assert callbacks(events) == []
    assert ("metric", 3) in events


@pytest.mark.parametrize(
    "change", ["revision", "target_revision", "origin_revision", "upstream_revision"]
)
def test_selector_identity_invalidation(tmp_path: Path, change: str) -> None:
    run, _, _ = workload(tmp_path, identity=Identity(upstream_revision="1"))
    run()
    options = replace(Identity(upstream_revision="1"), **{change: "2"})
    updated, events, seen = workload(tmp_path, identity=options)
    updated()
    assert ("selector", frozenset({"a", "b"})) in events
    assert ("mapping", frozenset({"a", "b"})) in events
    assert seen[-1] == {("a", "a-2"), ("b", "b-2")}


def test_independence_includes_changed_candidate_pool(
    tmp_path: Path, database: Database
) -> None:
    def compute(ids: tuple[str, ...]) -> tuple[set, set]:
        run, events, seen = workload(tmp_path)
        run(ids)
        assert ("query", frozenset(f"{key}-2" for key in ids)) in events
        keys = {
            row["value_key"]
            for row in database.rows["stored_results"]
            if row["node_id"] == "mapping"
        }
        b_keys = {
            row["value_key"]
            for row in database.rows["stored_partitions"]
            if row["value_key"] in keys and row["location_id"] == "b"
        }
        return b_keys, {pair for pair in seen[-1] if pair[0] == "b"}

    first = compute(("a", "b"))
    database.rows.clear()
    second = compute(("b", "c"))
    assert first == second
    assert first[1] == {("b", "b-2")}


def test_operation_parameters_are_normalized_and_invalidate(tmp_path: Path) -> None:
    run, _, _ = workload(tmp_path)
    run()
    explicit, events, _ = workload(tmp_path, identity=Identity(operation_count=2))
    explicit()
    assert callbacks(events) == []
    changed, events, seen = workload(tmp_path, identity=Identity(operation_count=3))
    changed()
    assert ("selector", frozenset({"a", "b"})) in events
    assert seen[-1] == {("a", "a-3"), ("b", "b-3")}


@pytest.mark.parametrize("target_cache", [False, True])
def test_candidate_records_support_later_exact_requests(
    tmp_path: Path, *, target_cache: bool
) -> None:
    run, events, seen = workload(tmp_path, target_cache=target_cache)
    run()
    events.clear()
    run(("a-2", "unused"), exact=True)
    assert seen[-1] == {"a-2", "unused"}
    assert events == ([] if target_cache else [("exact", frozenset({"a-2", "unused"}))])
