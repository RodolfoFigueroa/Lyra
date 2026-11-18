"""Effective direct inputs and attribute-derived targets through managed execution."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call

import pandas as pd
import pytest

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.errors import LocationProviderContractError, LocationSelectionError
from lyra.contracts.provider_scope import RecordReferences
from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.results import RunResult
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server import worker
from tests.effective_input_helpers import inputs, records, workload
from tests.frame_assertions import assert_logical_equal
from tests.queue_helpers import read_retained
from tests.sqlite_helpers import database
from tests.submission_execution_helpers import submit
from tests.worker_graph_integration_helpers import install_definitions, native_job

__all__ = ["database"]


def proposals() -> list[Intervention]:
    source_rows = records().assign(location_id=["x", "y"], target_id=["new", "new"])
    # The caller prepares a changed record and explicitly assigns it a fresh ID.
    replacement = (
        records()
        .iloc[[1]]
        .assign(location_id="new", count=pd.array([17], dtype="Int64"))
    )
    return [
        Intervention(id="links", catalog="sources", revision="1", add=source_rows),
        Intervention(
            id="replacement",
            catalog="targets",
            revision="1",
            add=replacement,
            remove=["b"],
        ),
    ]


@pytest.mark.usefixtures("database")
def test_direct_exact_ids_preserve_order_ownership_and_qualified_records(
    tmp_path: Path,
) -> None:
    trace = Mock()
    catalog = LocalRunner.from_definitions(workload(trace)).catalog
    registered = catalog.provider_registry["sources"]
    proposal = replace(proposals()[0], remove=["b"])
    with SQLiteStore(tmp_path / "records") as store:
        held = ProviderRecords(store)
        executor = GraphExecutor(
            catalog, catalog.implementations, Mock(), provider_records=held
        )

        def plan(ids: tuple[str, ...], *changes: Intervention) -> PreparedExecution:
            return prepare_execution(
                catalog,
                {"metric": "root", "inputs": inputs(*ids)},
                native_interventions=changes,
            )

        executor.acquire_sources(plan(("a", "b")))
        trace.reset_mock()
        accepted = plan(("x", "a", "x"), proposal)
        value = executor.acquire_sources(accepted)["source"]
        assert value.ids == ("x", "a")
        assert value.added_ids == {"x"}
        scope = accepted.request.interventions[0].effective_scope(registered.scope)
        assert value.contract.provider_scope == scope
        assert value.frame.target_id.tolist() == ["new", "a"]
        value.frame.loc[:, "target_id"] = "mutated"
        again = executor.acquire_sources(accepted)["source"]
        assert again.frame.target_id.tolist() == ["new", "a"]
        empty = executor.acquire_sources(plan((), proposal))["source"]
        assert empty.frame.empty
        assert empty.contract == value.contract
        with pytest.raises(LocationSelectionError, match=r"removed.*'b'"):
            executor.acquire_sources(plan(("x", "b"), proposal))
        trace.resolve.assert_not_called()
        trace.membership.assert_called_once_with("sources", ["b", "x", "y"])
        with pytest.raises(
            LocationProviderContractError,
            match=r"match requested IDs exactly.*'absent'",
        ):
            executor.acquire_sources(plan(("x", "absent"), proposal))
        trace.resolve.assert_called_once_with("sources", ["absent"])

        refs = RecordReferences(scope, frozenset({"x", "y"}))
        base = held.lookup(registered, ["a", "b", "x"]).set_index("location_id")
        assert base.target_id.to_dict() == {"a": "a", "b": "b"}
        additions = held.lookup(registered, ["x", "y"], references=refs)
        assert additions.set_index("location_id").target_id.to_dict() == {"x": "new"}


def test_exact_target_follows_unchanged_links_and_reuses_full_computations() -> None:
    trace = Mock()
    local = LocalRunner.from_definitions(workload(trace))
    changes = proposals()
    local.run("root", inputs=inputs("b"))
    trace.reset_mock()
    # The cached base row cannot satisfy the unchanged source link after removal.
    with pytest.raises(GraphExecutionError, match=r"removed.*'b'"):
        local.run("root", inputs=inputs("b"), interventions=changes[1:])
    trace.resolve.assert_not_called()
    trace.measure.assert_not_called()

    selected = inputs("x", "a", "y", "x")
    result = local.run_development("root", inputs=selected, interventions=changes)
    assert result.tables["rows"].value.tolist() == [35]
    trace.measure.assert_called_once_with([("x", "new"), ("a", "a"), ("y", "new")])
    assert trace.resolve.call_args_list == [
        call("sources", ["a"]),
        call("targets", ["a"]),
    ]
    assert all(
        node.outcome == "executed"
        for node in result.report.nodes
        if node.kind == "provider"
    )

    trace.reset_mock()
    warm = local.run("root", inputs=selected, interventions=changes[::-1])
    assert_logical_equal(warm.tables["rows"], result.tables["rows"])
    assert not trace.mock_calls
    added_only = local.run("root", inputs=inputs("x"), interventions=changes)
    assert added_only.tables["rows"].value.tolist() == [17]
    trace.resolve.assert_not_called()
    assert all(
        node.outcome == "skipped"
        for node in added_only.report.nodes
        if node.kind == "provider"
    )
    # A different set revision can give that same added target a different value.
    replacement = changes[1]
    assert replacement.add is not None
    revised = replace(
        replacement,
        revision="2",
        add=replacement.add.assign(count=pd.array([19], dtype="Int64")),
    )
    assert local.run(
        "root", inputs=selected, interventions=[changes[0], revised]
    ).tables["rows"].value.tolist() == [39]


@pytest.mark.usefixtures("worker_state")
def test_worker_resolves_inline_sources_and_id_only_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = Mock()
    _, runtime = install_definitions(tmp_path, monkeypatch, workload(trace))
    enqueue = Mock()
    runtime.jobs.enqueue = enqueue
    plan = prepare_execution(
        runtime.catalog.compiled,
        {"metric": "root", "inputs": inputs("y", "a")},
        native_interventions=proposals(),
    )

    def execute(transport: str, payload: dict) -> RunResult:
        assert asyncio.run(submit(runtime, transport, payload))
        envelope = enqueue.call_args.args[0]
        native_job(monkeypatch).id = envelope.job_id
        saved = worker.run_metric_task(envelope.model_dump(mode="json"))
        return read_retained(str(saved["result_ref"]))

    payload = plan.request.submission_payload()
    assert execute("http", payload).tables["rows"].value.tolist() == [18]
    trace.measure.assert_called_once_with([("y", "new"), ("a", "a")])
    assert trace.resolve.call_args_list == [
        call("sources", ["a"]),
        call("targets", ["a"]),
    ]

    trace.reset_mock()
    source, target = payload["interventions"]
    cached = execute("mcp", payload | {"interventions": [target, source]})
    assert cached.tables["rows"].value.tolist() == [18]
    assert {
        check.scope.provider_id: check.membership_lookup
        for check in cached.report.intervention_validations
    } == {"sources": "skipped", "targets": "skipped"}
    assert not trace.mock_calls

    # A new target revision fails as a whole even when no source is selected.
    revised = target | {"revision": "2", "remove": ["outside"]}
    with pytest.raises(GraphExecutionError, match=r"missing.*outside"):
        execute(
            "mcp", payload | {"inputs": inputs(), "interventions": [source, revised]}
        )
    trace.membership.assert_called_once_with("targets", ["new", "outside"])
    trace.measure.assert_not_called()
