"""Canonical selection stops at the native invocation cache boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.preparation import prepare_execution
from test_support.canonical_execution import canonical_workload
from test_support.location_resolution import frame_features, selection


@pytest.mark.parametrize("revision", [None, "source", "target"])
def test_routed_cache_boundary(tmp_path: Path, revision: str | None) -> None:
    work = canonical_workload("routed", 2, 1)
    executor = work.executor
    executor.cache = TableCache(tmp_path)
    plugin = executor.plugins["bench"]
    with (
        patch.object(type(plugin), "invoke", autospec=True) as metric,
        patch.object(type(plugin), "invoke_operation", autospec=True) as operation,
    ):
        # Delegate through the real implementation while observing cache misses.
        metric.side_effect = original_invoke
        operation.side_effect = original_operation
        executor.execute(work.prepared)
        executor.execute(work.prepared)
        assert metric.call_count == operation.call_count == 1
        if revision:
            updated = canonical_workload("routed", 2, 1, revision=revision)
            executor.catalog = updated.executor.catalog
            executor.plugins = updated.executor.plugins
        catalog = executor.catalog
        prepared = prepare_execution(
            catalog,
            {
                "metric": "one",
                "inputs": {"left": selection("0", "1")},
            },
        )
        executor.execute(prepared)
        assert operation.call_count == (1 if revision is None else 2)
        assert metric.call_count == (2 if revision == "target" else 1)
        native = prepare_execution(
            catalog,
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
        assert metric.call_count == (2 if revision == "target" else 1)
        assert prepared.inputs[0].binding.operations == ("select",)
        assert native.inputs[0].binding.operations == ()
        # A different public batch can select the same complete native contents.
        operation.side_effect = None
        operation.return_value = metric.call_args.args[1].locations["left"]
        equivalent = prepare_execution(
            catalog, {"metric": "one", "inputs": {"left": selection("different")}}
        )
        before = operation.call_count
        executor.execute(equivalent)
        assert operation.call_count == before + 1
        assert metric.call_count == (2 if revision == "target" else 1)
        executor.execute(equivalent)
        assert operation.call_count == before + 1
        assert metric.call_count == (2 if revision == "target" else 1)
        reordered = prepare_execution(
            catalog,
            {
                "metric": "one",
                "inputs": {
                    "left": selection(
                        "1", "0", collection="bench:target", unit_type="point"
                    )
                },
            },
        )
        executor.execute(reordered)
        assert metric.call_count == (3 if revision == "target" else 2)


original_invoke = BoundPlugin.invoke
original_operation = BoundPlugin.invoke_operation


def test_warm_metric_does_not_hide_selector_failure(tmp_path: Path) -> None:
    work = canonical_workload("routed", 2, 1)
    work.executor.cache = TableCache(tmp_path)
    work.executor.execute(work.prepared)
    changed_source = prepare_execution(
        work.executor.catalog,
        {"metric": "one", "inputs": {"left": selection("different")}},
    )
    plugin = work.executor.plugins["bench"]
    with (
        patch.object(
            type(plugin), "invoke_operation", side_effect=ValueError("selector failed")
        ) as operation,
        patch.object(type(plugin), "invoke") as metric,
        pytest.raises(GraphExecutionError, match="selector failed") as error,
    ):
        work.executor.execute(changed_source)
    operation.assert_called_once()
    metric.assert_not_called()
    assert error.value.node == "select"
    assert changed_source.inputs[0].binding.operations == ("select",)


def test_equal_ids_with_changed_contents_miss_metric_cache(tmp_path: Path) -> None:
    work = canonical_workload("native", 2, 1)
    work.executor.cache = TableCache(tmp_path)
    plugin = work.executor.plugins["bench"]
    provider = plugin.resources["locations"]
    with patch.object(type(plugin), "invoke", autospec=True) as metric:
        metric.side_effect = original_invoke
        work.executor.execute(work.prepared)
        changed = frame_features(["0", "1"])
        changed["original"] = ["changed", "contents"]
        with patch.object(provider, "resolve", return_value=changed):
            work.executor.execute(work.prepared)
            work.executor.execute(work.prepared)
        assert metric.call_count == 2
