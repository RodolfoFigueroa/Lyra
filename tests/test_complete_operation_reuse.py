"""Complete mapping lookup precedes source acquisition and preserves target policy."""

from pathlib import Path

import pytest

from lyra.runtime.execution import GraphExecutionError
from tests.operation_reuse_helpers import workload
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("empty", [False, True])
def test_full_hit_bypasses_source_and_preserves_groups(
    tmp_path: Path, *, empty: bool
) -> None:
    run, events, seen, mapping = workload(tmp_path)
    if not empty:
        mapping.update(a=("shared",), b=("shared",))
    run()
    assert ("mapping", frozenset({"a", "b"})) in events
    events.clear()
    run(("b", "a"), {"count": 2})
    assert events == ([] if empty else [("targets", frozenset({"shared"}))])
    assert set(zip(seen[-1].source_location_id, seen[-1].location_id, strict=True)) == (
        set() if empty else {("a", "shared"), ("b", "shared")}
    )


def test_changed_selection_and_parameters_miss(tmp_path: Path) -> None:
    run, events, _, mapping = workload(tmp_path, derived=True)
    mapping["a"] = ("shared",)
    run()
    events.clear()
    run(("a", "c"))
    assert ("mapping", frozenset({"a", "c"})) in events
    events.clear()
    run(("a", "c"), {"count": 3})
    assert ("mapping", frozenset({"a", "c"})) in events
    events.clear()
    run(("c", "a"), {"count": 3})
    assert not any(
        isinstance(event, tuple) and event[0] == "mapping" for event in events
    )


def test_zero_sources_skip_handler_and_reuse_complete_empty(tmp_path: Path) -> None:
    run, events, seen, _ = workload(tmp_path)
    run(())
    assert events == []
    again = run(())
    assert events == []
    assert seen[-1].empty
    assert any(
        item.node == "mapping" and item.outcome == "cache_hit"
        for item in again.provenance.execution.observations
    )


@pytest.mark.parametrize("mapping", [{"unknown": ()}, {"a": (1,)}, {"a": ("t", "t")}])
def test_invalid_mapping_cannot_publish_empty_success(
    tmp_path: Path, mapping: dict
) -> None:
    run, events, _, returned = workload(tmp_path)
    returned.update(mapping)
    with pytest.raises(GraphExecutionError):
        run()
    returned.clear()
    events.clear()
    run()
    assert ("mapping", frozenset({"a", "b"})) in events


def test_off_executes_complete_selection(tmp_path: Path) -> None:
    run, events, _, _ = workload(tmp_path, policy="off")
    run()
    events.clear()
    run()
    assert ("sources", frozenset({"a", "b"})) in events
    assert ("mapping", frozenset({"a", "b"})) in events


@pytest.mark.parametrize("target_cache", [False, True])
def test_target_records_follow_provider_policy(
    tmp_path: Path, *, target_cache: bool
) -> None:
    run, events, seen, mapping = workload(tmp_path, target_cache=target_cache)
    mapping.update(a=("shared",), b=("shared",))
    run()
    events.clear()
    run()
    assert events == ([] if target_cache else [("targets", frozenset({"shared"}))])
    assert len(seen[-1]) == 2


def test_damaged_mapping_fails_before_source_acquisition(
    tmp_path: Path, database: Database
) -> None:
    run, events, _, mapping = workload(tmp_path)
    mapping["a"] = ("shared",)
    run()
    mapping_keys = {
        row["value_key"]
        for row in database.rows["stored_results"]
        if row["node_id"] == "mapping"
    }
    database.rows["stored_partitions"][:] = [
        row
        for row in database.rows["stored_partitions"]
        if row["value_key"] not in mapping_keys
    ]
    events.clear()
    with pytest.raises(GraphExecutionError, match="Damaged operation mapping"):
        run()
    assert events == []
