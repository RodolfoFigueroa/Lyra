"""Provenance describes current visits independently of cached native outputs."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from lyra.sdk.cache import TableCache
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.models.location import Locations
from lyra.sdk.preparation import prepare_execution

from benchmarks.execution import CanonicalExecutionSuite
from tests.location_helpers import output_schema
from tests.test_location_resolution import frame_features, selection


def test_current_run_cache_provenance(tmp_path: Path) -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    executor = suite.executor
    executor.cache = TableCache(tmp_path)
    executor.execute(suite.prepared)
    cold = executor.provenance
    assert cold is not None
    assert cold.succeeded
    assert {o.outcome for o in cold.observations} == {"started", "completed"}
    assert cold.native_inputs[0].count == 2
    assert cold.accepted is suite.prepared
    assert json.loads(cold.accepted.metadata_json)["nodes"]
    executor.execute(suite.prepared)
    warm = executor.provenance
    assert warm is not None
    assert {o.outcome for o in warm.observations} == {"cache_hit"}
    assert warm.native_inputs == cold.native_inputs
    native = prepare_execution(
        suite.catalog,
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
    assert len(direct.observations) == 1
    assert direct.observations[0].outcome == "cache_hit"
    assert direct.native_inputs == cold.native_inputs
    assert native.inputs[0].binding.operations == ()
    assert cold.accepted.inputs[0].binding.operations == ("select",)


def test_failure_retains_completed_selector() -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    plugin = suite.executor.plugins["bench"]
    with (
        patch.object(type(plugin), "invoke", side_effect=ValueError("failed")),
        pytest.raises(GraphExecutionError) as error,
    ):
        suite.executor.execute(suite.prepared)
    provenance = error.value.provenance
    assert provenance is suite.executor.provenance
    assert provenance is not None
    assert not provenance.succeeded
    assert provenance.accepted is suite.prepared
    assert [(o.node, o.outcome) for o in provenance.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("one", "started"),
    ]
    assert provenance.native_inputs[0].count == 2


def test_shared_selector_is_observed_once() -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 2)
    suite.executor.execute(suite.prepared)
    provenance = suite.executor.provenance
    assert provenance is not None
    assert [(o.node, o.outcome) for o in provenance.observations] == [
        ("select", "started"),
        ("select", "completed"),
        ("select", "shared_reuse"),
        ("two", "started"),
        ("two", "completed"),
    ]
    assert len(provenance.native_inputs) == 2


def test_empty_native_summary() -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    plugin = suite.executor.plugins["bench"]
    suite.executor.execute(suite.prepared)

    assert suite.executor.graph.nodes["select"].node.kind == "location_operation"
    contract = suite.executor.graph.nodes["select"].node.output_contract
    assert contract is not None
    value = Locations.from_geodataframe(
        frame_features(["0"]).iloc[:0],
        collection="bench:target",
        unit_type="point",
        schema=output_schema(original=str),
    ).with_contract(contract)
    with patch.object(type(plugin), "invoke_operation", return_value=value):
        suite.executor.execute(suite.prepared)
    provenance = suite.executor.provenance
    assert provenance is not None
    assert provenance.native_inputs[0].count == 0
    assert provenance.native_inputs[0].fingerprint


def test_rejected_root_replaces_previous_provenance() -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    suite.executor.execute(suite.prepared)
    previous = suite.executor.provenance
    rejected = replace(suite.prepared, node="select")
    with pytest.raises(GraphExecutionError) as error:
        suite.executor.execute(rejected)
    current = suite.executor.provenance
    assert current is not None
    assert current is not previous
    assert error.value.provenance is current
    assert current.accepted is rejected
    assert not current.succeeded
    assert current.observations == ()
    assert current.native_inputs == ()


def test_source_metadata_is_captured_at_acceptance() -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    provider = suite.catalog.location_provider
    assert provider is not None
    contract = provider.declaration.contracts[0]
    contract.observation_period = "2024"
    contract.release = "release-one"
    prepared = prepare_execution(
        suite.catalog,
        {"metric": "one", "inputs": {"left": selection("0", "1")}},
    )
    contract.release = "release-two"
    suite.executor.execute(prepared)
    provenance = suite.executor.provenance
    assert provenance is not None
    assert provenance.accepted is not None
    metadata = json.loads(provenance.accepted.metadata_json)
    captured = next(c for c in metadata["locations"] if c["source"] == contract.source)
    assert captured["observation_period"] == "2024"
    assert captured["release"] == "release-one"
    assert all(b.revision is None for b in provenance.accepted.revisions.bindings)
