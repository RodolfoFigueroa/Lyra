"""Managed local reports cover reuse, empty selections, and failure boundaries."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from rq.job import Job

from lyra.contracts.errors import ExecutionPersistenceError
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.results import ResultStore
from lyra.sdk import LocationDependency, PluginDefinition, loading, metric
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.selector_reuse_helpers import Identity, selector_definitions
from tests.sqlite_helpers import Database, database

__all__ = ["database"]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("failure", [None, "calculation", "storage"])
def test_location_reports_and_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None,
    database: Database,
    *,
    private: bool,
) -> None:
    events = []
    cause = OSError("deliberate failure")

    @metric(
        name="measure",
        description="Measure places",
        private=private,
        cache="per_location",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"data": schema()},
    )
    def measure(places: gpd.GeoDataFrame) -> pd.DataFrame:
        # User code must never execute inside a storage transaction.
        assert not database.in_transaction
        if failure == "calculation":
            raise cause
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )

    definitions = provider_execution_definitions(events)
    definitions["measure"] = PluginDefinition(metrics=[measure])
    path = tmp_path / "results.sqlite"
    runner = LocalRunner.from_definitions(definitions, result_store_path=path)
    run = runner.run_development if private else runner.run
    if failure == "storage":
        monkeypatch.setattr(MetricTables, "publish", Mock(side_effect=cause))
    if failure is not None:
        error = (
            ExecutionPersistenceError if failure == "storage" else GraphExecutionError
        )
        with pytest.raises(error) as caught:
            run("measure", inputs={"places": {"provider": "target", "ids": ["a"]}})
        assert caught.value.__cause__ is cause
        assert "close:target" in events
        assert runner.inspect() is not None
        assert not database.rows.get("final_results")
        return

    for ids, expected in [
        ([], "skipped"),
        (["a"], "executed"),
        (["a", "b"], "mixed"),
        (["a", "b"], "reused"),
    ]:
        result = run("measure", inputs={"places": {"provider": "target", "ids": ids}})
        report = result.report
        root = next(node for node in report.nodes if node.use_id == report.root_use_id)
        assert root.outcome == expected
        assert report.execution_id == result.metadata.execution_id
        assert report.request.inputs["places"].ids == ids
        assert result.tables["data"].location_id.tolist() == ids
        assert root.outputs == result.metadata.outputs
        path.touch()
        events_before = events.copy()
        with ResultStore.open(path) as store:
            described = store.describe(result.metadata.result_ref)
            assert described.model_dump() == result.metadata.model_dump(
                exclude={"store_path"}
            )
            reopened = store.read_result(result.metadata.result_ref)
            assert reopened.report == report
            assert reopened.metadata == result.metadata
            pd.testing.assert_frame_equal(
                reopened.tables["data"], result.tables["data"].convert_dtypes()
            )
            attached = reopened.report
            reopened_root = next(
                node for node in attached.nodes if node.use_id == attached.root_use_id
            )
            reopened_root.inputs["places"].source_use_id = "edited"
            attached.request.inputs["places"].ids.append("edited")
            reopened_root.outputs["data"].table_schema.columns.clear()
            assert reopened.metadata == result.metadata
            assert store.read_result(result.metadata.result_ref).report == report
        assert reopened.report is attached
        assert events == events_before
        root.outputs["data"].table_schema.columns[0].description = "edited"
        assert root.outputs != result.metadata.outputs


@pytest.mark.usefixtures("database")
def test_changed_declarations_preserve_reports_and_detached_operation_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "retained.sqlite"
    definitions, events, _ = selector_definitions(metric_cache="full")
    runner = LocalRunner.from_definitions(definitions, result_store_path=path)
    inputs = {"source": {"provider": "sources", "ids": ["a", "b"]}}
    original = runner.run("consumer", inputs=inputs)
    expected = original.report.model_copy(deep=True)
    changed, _, _ = selector_definitions(
        policy="off",
        target_cache=False,
        identity=Identity(revision="2", target_revision="2", origin_revision="2"),
    )
    newer = LocalRunner.from_definitions(changed, result_store_path=path).run(
        "consumer", inputs=inputs
    )
    assert newer.metadata.execution_id != original.metadata.execution_id
    path.touch()
    events.clear()
    forbidden = Mock(
        side_effect=AssertionError("retrieval requires no jobs or plugins")
    )
    monkeypatch.setattr(Job, "fetch", forbidden)
    monkeypatch.setattr(loading, "load_plugin_definition", forbidden)
    monkeypatch.setattr(loading, "load_installed_plugin_definition", forbidden)
    with ResultStore.open(path) as reader:
        assert (
            reader.describe(original.metadata.result_ref).outputs
            == original.metadata.outputs
        )
        restored = reader.read_result(original.metadata.result_ref)
        assert restored.report == expected
        report = restored.report
        report.request.inputs["source"].ids.append("edited")
        report.request.parameters["metric_count"] = 99
        root = next(n for n in report.nodes if n.use_id == report.root_use_id)
        root.parameters["metric_count"] = 98
        root.inputs["source"].source_use_id = "edited"
        root.inputs["source"].provider_scope = None
        root.outputs["result"].table_schema.columns.clear()
        operation = next(n.operation for n in report.nodes if n.node_id == "mapping")
        assert operation is not None
        operation.mapping = "skipped"
        operation.target_scope = operation.target_scope.model_copy(
            update={"provider_id": "edited"}
        )
        assert reader.read_report(original.metadata.execution_id) == expected
        assert reader.read_result(original.metadata.result_ref).report == expected
    # Attached metadata and native values remain independent after closure.
    assert restored.metadata == original.metadata
    pd.testing.assert_frame_equal(
        restored.tables["result"], original.tables["result"].convert_dtypes()
    )
    assert events == []
    monkeypatch.setattr(
        ResultStore,
        "read_report",
        Mock(side_effect=AssertionError("unexpected lookup")),
    )
    assert restored.report is report
    assert report.nodes is report.nodes
    assert report.request.parameters is report.request.parameters
