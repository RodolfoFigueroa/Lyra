"""Direct operations reuse complete source partitions across overlapping requests."""

from pathlib import Path

import pandas as pd
import pytest

from lyra.runtime.execution import GraphExecutionError
from tests.operation_reuse_helpers import workload
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def pairs(frame: pd.DataFrame) -> set[tuple[str, str]]:
    """Return observable correspondence identities."""
    return set(zip(frame.source_location_id, frame.location_id, strict=True))


@pytest.mark.parametrize("target_cache", [False, True])
@pytest.mark.parametrize(
    ("empty_b", "omit_c"), [(False, False), (True, False), (False, True), (True, True)]
)
def test_overlapping_requests_compute_only_missing_sources(
    tmp_path: Path, *, target_cache: bool, empty_b: bool, omit_c: bool
) -> None:
    run, events, seen, mapping = workload(
        tmp_path, policy="per_location", target_cache=target_cache
    )
    mapping["a"] = ("shared",)
    if not empty_b:
        mapping["b"] = ("shared",)
    run()
    assert pairs(seen[-1]) == (
        {("a", "shared")} if empty_b else {("a", "shared"), ("b", "shared")}
    )
    events.clear()
    mapping.clear()
    if not omit_c:
        mapping["c"] = ("shared",)
    result = run(("b", "c"))
    outcomes = [(o.node, o.outcome) for o in result.provenance.execution.observations]
    assert ("mapping", "partial_hit") in outcomes
    assert ("mapping", "completed") in outcomes
    assert ("sources", frozenset({"c"})) in events
    assert ("mapping", frozenset({"c"})) in events
    assert [event for event in events if isinstance(event, tuple)] == [
        ("sources", frozenset({"c"})),
        ("mapping", frozenset({"c"})),
        *(
            []
            if target_cache or (empty_b and omit_c)
            else [("targets", frozenset({"shared"}))]
        ),
    ]
    expected = set()
    if not empty_b:
        expected.add(("b", "shared"))
    if not omit_c:
        expected.add(("c", "shared"))
    assert pairs(seen[-1]) == expected
    events.clear()
    result = run(("c", "b"))
    assert [
        (o.node, o.outcome)
        for o in result.provenance.execution.observations
        if o.node == "mapping"
    ] == [("mapping", "cache_hit")]
    assert events == (
        []
        if target_cache or (empty_b and omit_c)
        else [("targets", frozenset({"shared"}))]
    )
    assert pairs(seen[-1]) == expected


@pytest.mark.parametrize("invalid", [{"c": (1,)}, {"c": ("t", "t")}, {"b": ()}])
def test_invalid_fresh_groups_preserve_previous_completions(
    tmp_path: Path, invalid: dict, database: Database
) -> None:
    run, events, seen, mapping = workload(tmp_path, policy="per_location")
    mapping["b"] = ("shared",)
    run()
    before = [
        dict(row)
        for row in database.rows["stored_results"]
        if row["node_id"] == "mapping"
    ]
    mapping.clear()
    mapping.update(invalid)
    with pytest.raises(GraphExecutionError):
        run(("b", "c"))
    assert [
        row
        for row in database.rows.get("stored_results", [])
        if row["node_id"] == "mapping"
    ] == before
    events.clear()
    run()
    assert events == [("targets", frozenset({"shared"}))]
    assert pairs(seen[-1]) == {("b", "shared")}
    events.clear()
    mapping.clear()
    run(("b", "c"))
    assert ("mapping", frozenset({"c"})) in events
    assert pairs(seen[-1]) == {("b", "shared")}


def test_empty_requests_create_no_partition_computations(
    tmp_path: Path, database: Database
) -> None:
    run, events, seen, _ = workload(tmp_path, policy="per_location")
    run(())
    run(())
    assert events == []
    assert seen[-1].empty
    assert not [
        row
        for row in database.rows.get("stored_results", [])
        if row["node_id"] == "mapping"
    ]


def test_partition_identity_is_independent_of_physical_batch(
    tmp_path: Path, database: Database
) -> None:
    def compute(ids: tuple[str, ...], path: Path) -> tuple[str, set[tuple[str, str]]]:
        run, events, seen, mapping = workload(
            path, policy="per_location", independent=True
        )
        mapping.update(a=("a-target",), b=("shared",), c=("c-target",))
        run(ids)
        assert ("mapping", frozenset(ids)) in events
        keys = {
            row["value_key"]
            for row in database.rows["stored_results"]
            if row["node_id"] == "mapping"
        }
        b_key = next(
            row["value_key"]
            for row in database.rows["stored_partitions"]
            if row["value_key"] in keys and row["location_id"] == "b"
        )
        return b_key, {pair for pair in pairs(seen[-1]) if pair[0] == "b"}

    first = compute(("a", "b"), tmp_path / "first")
    # Independent stores force both batches to compute B rather than reuse it.
    database.rows.clear()
    second = compute(("b", "c"), tmp_path / "second")
    assert first == second
    assert first[1] == {("b", "shared")}
