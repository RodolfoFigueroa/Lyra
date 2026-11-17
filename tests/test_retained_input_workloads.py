"""Benchmark workloads preserve storage sharing and complete mixed selections."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputSelection
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_schema import Registration
from test_support.retained_input_workloads import retained_inputs, retained_workload
from tests.geoparquet_integration_helpers import assert_exports, assert_remote_outputs
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.sqlite_helpers import Database, database

__all__ = ["database", "native_api"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("policy", ["full", "per_location"])
def test_shared_membership_and_dense_pairs(
    tmp_path: Path,
    database: Database,
    policy: CachePolicy,
    native_api: NativeAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = retained_workload(derived=True, policy=policy)
    path = tmp_path / "results.sqlite"
    runner = LocalRunner.from_definitions(
        {"bench": work.plugin}, result_store_path=path
    )
    fresh = runner.run("root", inputs=retained_inputs(9, 20))
    work.calls.clear()
    mixed = runner.run("root", inputs=retained_inputs(10, 20))
    expected = {"s9"} if policy == "per_location" else {f"s{i}" for i in range(10)}
    assert [ids for name, ids in work.calls if name == "root"] == [frozenset(expected)]
    before = {name: len(rows) for name, rows in database.rows.items()}
    work.calls.clear()
    reused = runner.run("root", inputs=retained_inputs(10, 20))
    assert work.calls == []
    # Explicit storage requirements: repetitions share values and memberships;
    # only the per-execution final publication may grow.
    assert {
        name: len(rows)
        for name, rows in database.rows.items()
        if name != "final_results"
    } == {name: count for name, count in before.items() if name != "final_results"}
    selections = [
        InputSelection.model_validate_json(row["description"])
        for row in database.rows["input_selections"]
    ]
    shared = [selection for selection in selections if "r0" in selection.records]
    assert len(shared) == 1
    assert len(shared[0].records) == 20
    registrations = [
        Registration.model_validate_json(row["description"])
        for row in database.rows["storage_registrations"]
    ]
    for registration in registrations:
        rows = database.rows["data_" + registration.key]
        if registration.kind == "provider":
            assert len(rows) == 130  # Each source, reference and target is stored once.
        if registration.kind == "operation":
            assert all("geometry" not in row for row in rows)
    path.touch()
    with ResultStore.open(path) as reader:
        for result in (fresh, mixed, reused):
            assert_exports(reader, result.metadata.result_ref, tmp_path)
        assert work.calls == []
        assert (
            len({result.metadata.result_ref for result in (fresh, mixed, reused)}) == 3
        )
        pairs = reader.read_location_input(mixed.metadata.result_ref, "targets")
        assert set(zip(pairs.source_location_id, pairs.location_id, strict=True)) == {
            (f"s{i}", f"t{j}") for i in range(10) for j in range(100)
        }
        assert (
            len(reader.read_location_input(mixed.metadata.result_ref, "reference"))
            == 20
        )

    assert_remote_outputs(
        path, [fresh, mixed, reused], native_api, monkeypatch, exports_only=True
    )
    assert work.calls == []


def test_changed_registration_preserves_captured_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "results.sqlite"
    original = retained_workload(derived=False)
    runner = LocalRunner.from_definitions(
        {"bench": original.plugin}, result_store_path=path
    )
    first = runner.run("root", inputs=retained_inputs(2, 3))
    changed = retained_workload(derived=False, revision="2")
    runner = LocalRunner.from_definitions(
        {"bench": changed.plugin}, result_store_path=path
    )
    second = runner.run("root", inputs=retained_inputs(2, 3))
    path.touch()
    monkeypatch.setattr(
        runner, "run", Mock(side_effect=AssertionError("No execution on read"))
    )
    with ResultStore.open(path) as reader:
        for result, revision in [(first, "1"), (second, "2")]:
            assert_exports(reader, result.metadata.result_ref, tmp_path)
            description = reader.describe_result(result.metadata.result_ref)
            assert (
                description.location_inputs["main"].provider_scope.code_revision
                == revision
            )
            assert set(
                reader.read_location_input(
                    result.metadata.result_ref, "main"
                ).location_id
            ) == {"s0", "s1"}
