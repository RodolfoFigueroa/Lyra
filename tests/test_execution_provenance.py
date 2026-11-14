"""Provenance describes current visits independently of cached native outputs."""

from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.contracts.origins import Origin
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.locations import LocationProvider
from test_support.canonical_execution import canonical_workload
from test_support.location_resolution import selection


def test_current_run_cache_provenance(tmp_path: Path) -> None:
    work = canonical_workload("routed", 2, 1)
    executor = work.executor
    executor.store = SQLiteStore(tmp_path / "values.sqlite")
    executor.execute(work.prepared)
    cold = executor.provenance
    assert cold is not None
    assert cold.execution.succeeded
    assert {o.outcome for o in cold.execution.observations} == {"started", "completed"}
    assert cold.execution.native_inputs[0].count == 2
    assert cold.accepted is not None
    assert cold.accepted == work.prepared
    assert cold.accepted.outputs
    executor.execute(work.prepared)
    warm = executor.provenance
    assert warm is not None
    assert {o.outcome for o in warm.execution.observations} == {"cache_hit"}
    assert warm.execution.native_inputs == ()
    native = prepare_execution(
        work.executor.catalog,
        {
            "metric": "one",
            "inputs": {"left": selection("0", "1", provider="bench_target")},
        },
    )
    executor.execute(native)
    direct = executor.provenance
    assert direct is not None
    assert direct.accepted == native
    assert [o.outcome for o in direct.execution.observations] == [
        "started",
        "completed",
    ]
    executor.execute(native)
    repeated = executor.provenance
    assert repeated is not None
    assert [o.outcome for o in repeated.execution.observations] == ["cache_hit"]
    assert direct.execution.native_inputs == cold.execution.native_inputs
    assert (
        direct.execution.native_inputs[0].count == cold.execution.native_inputs[0].count
    )
    assert native.inputs[0].binding.operations == ()
    assert cold.accepted.inputs[0].binding.operations == ("select",)


def test_failure_retains_completed_selector() -> None:
    work = canonical_workload("routed", 2, 1)
    plugin = work.executor.plugins["bench"]
    with (
        patch.object(type(plugin), "invoke", side_effect=ValueError("failed")),
        pytest.raises(GraphExecutionError) as error,
    ):
        work.executor.execute(work.prepared)
    provenance = error.value.provenance
    assert provenance == work.executor.provenance
    assert provenance is not None
    assert not provenance.execution.succeeded
    assert provenance.accepted == work.prepared
    assert [(o.node, o.outcome) for o in provenance.execution.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("one", "started"),
    ]
    assert provenance.execution.native_inputs[0].count == 2
    assert provenance.accepted is not None
    provenance.accepted.outputs.clear()
    retained = work.executor.provenance
    assert retained is not None
    assert retained.accepted == work.prepared


def test_shared_selector_is_observed_once() -> None:
    work = canonical_workload("routed", 2, 2)
    work.executor.execute(work.prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert [(o.node, o.outcome) for o in provenance.execution.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("two", "started"),
        ("two", "completed"),
    ]
    assert len(provenance.execution.native_inputs) == 2


def test_empty_native_summary() -> None:
    work = canonical_workload("routed", 2, 1)
    plugin = work.executor.plugins["bench"]
    work.executor.execute(work.prepared)

    with patch.object(
        type(plugin),
        "invoke_operation",
        return_value={"0": (), "1": ()},
    ):
        work.executor.execute(work.prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert provenance.execution.native_inputs[0].count == 0
    assert provenance.execution.native_inputs[0].count >= 0


def test_rejected_root_replaces_previous_provenance() -> None:
    work = canonical_workload("native", 2, 1)
    work.executor.execute(work.prepared)
    previous = work.executor.provenance
    rejected = work.prepared.model_copy(update={"node": "select"})
    with pytest.raises(GraphExecutionError) as error:
        work.executor.execute(rejected)
    current = work.executor.provenance
    assert current is not None
    assert current is not previous
    assert error.value.provenance == current
    assert current.accepted == rejected
    assert not current.execution.succeeded
    assert current.execution.observations == ()
    assert current.execution.native_inputs == ()


def test_source_metadata_is_captured_at_acceptance() -> None:
    work = canonical_workload("routed", 2, 1)
    plugin = work.executor.plugins["bench"]
    provider = plugin.resources["source"]
    assert issubclass(provider, LocationProvider)
    provider.origins = (
        Origin(id="fixture", revision="2024", description="release-one"),
    )
    work.executor.catalog = compile_live_catalog({"bench": plugin.definition})
    work.executor.plugins = work.executor.catalog.implementations
    prepared = prepare_execution(
        work.executor.catalog,
        {"metric": "one", "inputs": {"left": selection("0", "1")}},
    )
    provider.origins = (
        Origin(id="fixture", revision="2025", description="release-two"),
    )
    with pytest.raises(GraphExecutionError, match="provider implementation"):
        work.executor.execute(prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert provenance.accepted is not None
    metadata = provenance.accepted.model_dump(mode="json")
    captured = next(
        c
        for c in metadata["locations"]
        if c["provider_scope"]["provider_id"] == "test_units"
    )
    assert captured["provider_scope"]["origins"] == [
        {"id": "fixture", "revision": "2024", "description": "release-one"}
    ]
