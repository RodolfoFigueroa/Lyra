"""Provenance describes current visits independently of cached native outputs."""

from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.planning.preparation import prepare_execution
from test_support.canonical_execution import canonical_workload
from test_support.location_helpers import output_schema
from test_support.location_resolution import frame_features, selection


def test_current_run_cache_provenance(tmp_path: Path) -> None:
    work = canonical_workload("routed", 2, 1)
    executor = work.executor
    executor.cache = TableCache(tmp_path)
    executor.execute(work.prepared)
    cold = executor.provenance
    assert cold is not None
    assert cold.execution.succeeded
    assert {o.outcome for o in cold.execution.observations} == {"started", "completed"}
    assert cold.execution.native_inputs[0].count == 2
    assert cold.accepted is work.prepared
    assert cold.accepted.contracts.model_dump(mode="json")["nodes"]
    executor.execute(work.prepared)
    warm = executor.provenance
    assert warm is not None
    assert {o.outcome for o in warm.execution.observations} == {"cache_hit"}
    assert warm.execution.native_inputs == cold.execution.native_inputs
    native = prepare_execution(
        work.executor.catalog,
        {
            "metric": "one",
            "inputs": {
                "left": selection(
                    "0", "1", collection="bench:target", unit_type="point"
                )
            },
        },
    )
    executor.execute(native)
    direct = executor.provenance
    assert direct is not None
    assert direct.accepted is native
    assert len(direct.execution.observations) == 1
    assert direct.execution.observations[0].outcome == "cache_hit"
    assert direct.execution.native_inputs == cold.execution.native_inputs
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
    assert provenance is work.executor.provenance
    assert provenance is not None
    assert not provenance.execution.succeeded
    assert provenance.accepted is work.prepared
    assert [(o.node, o.outcome) for o in provenance.execution.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("one", "started"),
    ]
    assert provenance.execution.native_inputs[0].count == 2


def test_shared_selector_is_observed_once() -> None:
    work = canonical_workload("routed", 2, 2)
    work.executor.execute(work.prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert [(o.node, o.outcome) for o in provenance.execution.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("select", "shared_reuse"),
        ("two", "started"),
        ("two", "completed"),
    ]
    assert len(provenance.execution.native_inputs) == 2


def test_empty_native_summary() -> None:
    work = canonical_workload("routed", 2, 1)
    plugin = work.executor.plugins["bench"]
    work.executor.execute(work.prepared)

    assert work.executor.graph.nodes["select"].node.kind == "location_operation"
    contract = work.executor.graph.nodes["select"].node.output_contract
    assert contract is not None
    value = (
        output_schema(original=str)
        .build(
            frame_features(["0"]).iloc[:0], collection="bench:target", unit_type="point"
        )
        .with_contract(contract)
    )
    with patch.object(type(plugin), "invoke_operation", return_value=value):
        work.executor.execute(work.prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert provenance.execution.native_inputs[0].count == 0
    assert provenance.execution.native_inputs[0].fingerprint


def test_rejected_root_replaces_previous_provenance() -> None:
    work = canonical_workload("routed", 2, 1)
    work.executor.execute(work.prepared)
    previous = work.executor.provenance
    rejected = work.prepared.model_copy(update={"node": "select"})
    with pytest.raises(GraphExecutionError) as error:
        work.executor.execute(rejected)
    current = work.executor.provenance
    assert current is not None
    assert current is not previous
    assert error.value.provenance is current
    assert current.accepted is rejected
    assert not current.execution.succeeded
    assert current.execution.observations == ()
    assert current.execution.native_inputs == ()


def test_source_metadata_is_captured_at_acceptance() -> None:
    work = canonical_workload("routed", 2, 1)
    provider = work.executor.catalog.location_provider
    assert provider is not None
    contract = provider.declaration.contracts[0]
    contract.observation_period = "2024"
    contract.release = "release-one"
    prepared = prepare_execution(
        work.executor.catalog,
        {"metric": "one", "inputs": {"left": selection("0", "1")}},
    )
    contract.release = "release-two"
    work.executor.execute(prepared)
    provenance = work.executor.provenance
    assert provenance is not None
    assert provenance.accepted is not None
    metadata = provenance.accepted.contracts.model_dump(mode="json")
    captured = next(c for c in metadata["locations"] if c["source"] == contract.source)
    assert captured["observation_period"] == "2024"
    assert captured["release"] == "release-one"
    assert all(b.revision is None for b in provenance.accepted.revisions.bindings)
