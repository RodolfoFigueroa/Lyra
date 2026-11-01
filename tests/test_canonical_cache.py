"""Canonical selection stops at the native invocation cache boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest
from lyra.sdk import PluginDefinition
from lyra.sdk.cache import TableCache
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.execution import GraphExecutionError
from lyra.sdk.preparation import prepare_execution

from benchmarks.execution import CanonicalExecutionSuite
from tests.test_location_resolution import frame_features, selection


@pytest.mark.parametrize("revision", [None, "source", "target"])
def test_routed_cache_boundary(tmp_path: Path, revision: str | None) -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    executor = suite.executor
    executor.cache = TableCache(tmp_path)
    plugin = executor.plugins["bench"]
    with (
        patch.object(type(plugin), "invoke", autospec=True) as metric,
        patch.object(type(plugin), "invoke_operation", autospec=True) as operation,
    ):
        # Delegate through the real implementation while observing cache misses.
        metric.side_effect = original_invoke
        operation.side_effect = original_operation
        executor.execute(suite.prepared)
        executor.execute(suite.prepared)
        assert metric.call_count == operation.call_count == 1
        if revision:
            collection = "test:units" if revision == "source" else "bench:target"
            for entry in executor.graph.nodes.values():
                for contract in (
                    *entry.node.location_contracts.values(),
                    *(
                        ()
                        if entry.node.output_contract is None
                        else (entry.node.output_contract,)
                    ),
                ):
                    if contract.family.collection == collection:
                        contract.data_revision = "updated"
            assert executor.location_provider is not None
            for contract in executor.location_provider.declaration.contracts:
                if contract.family.collection == collection:
                    contract.data_revision = "updated"
        catalog = compile_catalog(suite.catalog.manifests)
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


original_invoke = PluginDefinition.invoke
original_operation = PluginDefinition.invoke_operation


def test_warm_metric_does_not_hide_selector_failure(tmp_path: Path) -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("routed", 2, 1)
    suite.executor.cache = TableCache(tmp_path)
    suite.executor.execute(suite.prepared)
    changed_source = prepare_execution(
        suite.catalog,
        {"metric": "one", "inputs": {"left": selection("different")}},
    )
    plugin = suite.executor.plugins["bench"]
    with (
        patch.object(
            type(plugin), "invoke_operation", side_effect=ValueError("selector failed")
        ) as operation,
        patch.object(type(plugin), "invoke") as metric,
        pytest.raises(GraphExecutionError, match="selector failed") as error,
    ):
        suite.executor.execute(changed_source)
    operation.assert_called_once()
    metric.assert_not_called()
    assert error.value.node == "select"
    assert changed_source.inputs[0].binding.operations == ("select",)


def test_equal_ids_with_changed_contents_miss_metric_cache(tmp_path: Path) -> None:
    suite = CanonicalExecutionSuite()
    suite.setup("native", 2, 1)
    suite.executor.cache = TableCache(tmp_path)
    plugin = suite.executor.plugins["bench"]
    provider = plugin.resources["locations"]
    with patch.object(type(plugin), "invoke", autospec=True) as metric:
        metric.side_effect = original_invoke
        suite.executor.execute(suite.prepared)
        changed = frame_features(["0", "1"])
        changed["original"] = ["changed", "contents"]
        with patch.object(provider, "resolve", return_value=changed):
            suite.executor.execute(suite.prepared)
            suite.executor.execute(suite.prepared)
        assert metric.call_count == 2
