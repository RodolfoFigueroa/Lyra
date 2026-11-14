"""Persistent reuse ordering and isolation from storage failures."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Annotated
from unittest.mock import Mock

import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.errors import MetricResultError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.contracts.resources import IOConfig
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning import computation
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import DamagedDataError, SQLiteStore
from lyra.sdk.dependencies import Dependency
from lyra.sdk.resources import BaseIO, Resource
from test_support.declarations import PluginDefinition, metric
from test_support.location_resolution import SELECTED
from test_support.manifest_catalog import compile_catalog
from tests.cache_helpers import SCHEMA
from tests.frame_assertions import assert_logical_equal
from tests.graph_execution_helpers import executor
from tests.location_cache_graph import SELECTION
from tests.location_cache_graph import setup_graph as location_graph
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.accepted import PreparedExecution
    from lyra.runtime.arguments import PreparedResolvedInvocation


def setup_graph(
    tmp_path: Path,
) -> tuple[
    GraphExecutor, PreparedResolvedInvocation, SQLiteStore, list[str], list[bool]
]:
    events: list[str] = []
    broken = [False]

    @metric(
        code_revision="1",
        name="source",
        description="Source",
        outputs={"source": SCHEMA},
    )
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [1]})

    @metric(
        code_revision="1",
        name="consumer",
        description="Consumer",
        outputs={"consumer": SCHEMA},
        inputs={"table": Dependency("source", producer="source")},
    )
    def consumer(table: pd.DataFrame) -> pd.DataFrame:
        events.append("consumer")
        return pd.DataFrame({"wrong": [1]}) if broken[0] else table

    runner, plugin = executor(consumer, source)
    cache = SQLiteStore(tmp_path / "values.sqlite")
    runner.store = cache
    root = plugin.prepare_resolved_invocation("consumer", {})
    root = replace(
        root,
        parameters=None,
        bound_use=bind_execution(
            runner.catalog, prepare_execution(runner.catalog, {"metric": "consumer"})
        ),
    )
    assert root.bound_use is not None
    return runner, root, cache, events, broken


def test_hit_skips_dependency_work(tmp_path: Path) -> None:
    runner, root, _, events, _ = setup_graph(tmp_path)
    first = runner.execute(root)
    assert events == ["source", "consumer"]
    events.clear()
    runner.context_factory = Mock(side_effect=AssertionError("context acquired"))
    again = runner.execute(root)
    assert events == []
    assert_logical_equal(first.outputs["consumer"], again.outputs["consumer"])


def test_damaged_cache_does_not_recompute(tmp_path: Path, database: Database) -> None:
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.execute(root)
    database.rows["stored_results"][-1]["output_rows"] = None
    events.clear()
    with pytest.raises(GraphExecutionError) as error:
        runner.execute(root)
    assert isinstance(error.value.__cause__, DamagedDataError)
    assert events == []


def test_invalid_handler_is_not_published_and_dependencies_survive(
    tmp_path: Path, database: Database
) -> None:
    runner, root, _, events, broken = setup_graph(tmp_path)
    broken[0] = True
    with pytest.raises(GraphExecutionError) as error:
        runner.execute(root)
    assert isinstance(error.value.__cause__, MetricResultError)
    assert {row["node_id"] for row in database.rows["stored_results"]} == {"source"}
    events.clear()
    broken[0] = False
    runner.execute(root)
    assert events == ["consumer"]


def test_location_preparation_runs_before_persistent_hit(tmp_path: Path) -> None:
    local, cache, _, events, _, _ = location_graph(tmp_path)
    runner = GraphExecutor(
        local.catalog, local.catalog.implementations, Mock(), store=cache
    )
    prepared = prepare_execution(
        runner.catalog,
        {"metric": "root", "inputs": SELECTION, "parameters": {"label": "a"}},
    )
    runner.execute(prepared)
    assert events == ["make", "source", "a"]
    events.clear()
    runner.execute(
        prepare_execution(
            runner.catalog,
            {
                "metric": "root",
                "inputs": SELECTION,
                "parameters": {"label": "b"},
            },
        )
    )
    assert events == ["b"]


def test_first_read_hit_skips_dependency_resources(tmp_path: Path) -> None:
    acquisitions = []

    class Support(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            acquisitions.append("setup")

    @metric(
        code_revision="1",
        name="source",
        description="Source",
        outputs={"source": SCHEMA},
    )
    def source(support: Annotated[BaseIO, Resource("support")]) -> pd.DataFrame:
        assert isinstance(support, Support)
        return pd.DataFrame({"value": [1]})

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={"table": Dependency("source", producer="source")},
    )
    def root(table: pd.DataFrame) -> pd.DataFrame:
        return table

    plugin = PluginDefinition(metrics=[source, root], resources={"support": Support})
    runner = GraphExecutor(
        LocalRunner.from_definitions({"p": plugin}).catalog,
        {"p": BoundPlugin(plugin)},
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    invocation = runner.plugins["p"].prepare_resolved_invocation("root", {})
    runner.execute(invocation)
    assert acquisitions == ["setup"]
    acquisitions.clear()
    runner.execute(invocation)
    assert acquisitions == []


def test_repeated_executions_preserve_entries(tmp_path: Path) -> None:
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.execute(root)
    old_key = runner.persistent_key(root.identity)
    events.clear()
    runner.execute(root)
    assert events == []
    assert old_key == runner.persistent_key(root.identity)
    events.clear()
    runner.execute(root)
    assert events == []


def test_namespace_contract_versions_and_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(computation, "installed_version", lambda _: None)
    runner, _, _, _, _ = setup_graph(tmp_path)

    def key() -> str:
        return bind_execution(
            runner.catalog, prepare_execution(runner.catalog, {"metric": "consumer"})
        ).identity

    original = key()
    manifests = dict(runner.catalog.manifests)
    runner.catalog = compile_catalog(dict(reversed(list(manifests.items()))))
    assert key() == original
    for manifest in manifests.values():
        for node in manifest.nodes:
            node.description = "Edited description"
    runner.catalog = compile_catalog(manifests)
    assert key() == original
    monkeypatch.setattr(computation, "installed_version", lambda _: "2")
    runner.catalog = compile_catalog(manifests)
    assert key() == original
    manifests["plugin_1"].nodes[0].code_revision = "2"
    runner.catalog = compile_catalog(manifests)
    assert key() != original


@pytest.mark.parametrize(
    "change", ["provider_contract", "provider_version", "producer_revision"]
)
def test_only_participating_versions_invalidate_seeded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    versions: dict[str, str] = {}
    monkeypatch.setattr(computation, "installed_version", versions.get)
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.store = SQLiteStore(tmp_path / "values.sqlite")
    manifests = dict(runner.catalog.manifests)
    manifests[SELECTED.distribution] = PluginManifest(
        plugin=PluginInfo(name=SELECTED.distribution, version="test"),
        factory="test:create",
        providers=(SELECTED.declaration,),
        resources={SELECTED.resource: {}, "other": {}},
    )
    runner.catalog = compile_catalog(manifests)
    runner.execute(root)
    events.clear()
    runner.execute(root)
    assert events == []
    if change == "provider_contract":
        manifests[SELECTED.distribution].providers = (
            SELECTED.declaration.model_copy(update={"resource": "other"}),
        )
    elif change == "producer_revision":
        manifests["plugin_1"].nodes[0].code_revision = "2"
        implementation = runner.plugins["plugin_1"]
        manifest = implementation.node_manifest("source")
        assert manifest is not None
        manifest.code_revision = "2"
        monkeypatch.setattr(implementation, "node_manifest", lambda _: manifest)
        descriptor = implementation.definition.implementation("source")
        monkeypatch.setattr(
            implementation.definition,
            "implementation",
            lambda _: replace(descriptor, manifest=manifest),
        )
    else:
        versions[SELECTED.distribution] = "2"
    runner.catalog = compile_catalog(manifests)
    runner.execute(root)
    assert events == (["source", "consumer"] if change == "producer_revision" else [])


def test_location_repeated_execution_preserves_reuse(tmp_path: Path) -> None:
    local, cache, _, events, _, _ = location_graph(tmp_path)
    runner = GraphExecutor(
        local.catalog,
        local.catalog.implementations,
        Mock(),
        store=cache,
    )

    def prepared() -> PreparedExecution:
        return prepare_execution(
            runner.catalog,
            {
                "metric": "root",
                "inputs": SELECTION,
                "parameters": {"label": "a"},
            },
        )

    first = runner.execute(prepared())
    references = tuple(c for c in first.completions if c.node == "root")
    assert references
    events.clear()
    runner.execute(prepared())
    assert events == []
    runner.execute(prepared())
    assert events == []
    assert runner.execute(prepared()).completions == references
    events.clear()
    runner.execute(prepared())
    assert events == []


def test_dependency_edges_keep_native_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.store = None
    conversion = Mock(side_effect=AssertionError("Unexpected storage conversion"))
    monkeypatch.setattr("lyra.data.arrow_conversion.to_arrow", conversion)
    assert runner.execute(root).outputs["consumer"].value.tolist() == [1]
    assert events.count("source") == 1
    conversion.assert_not_called()


@pytest.mark.parametrize("initial", [None, "first"])
def test_target_revision_invalidates_transitive_cache(
    tmp_path: Path, initial: str | None, database: Database
) -> None:
    local, _, _, events, _, _ = location_graph(tmp_path)

    def execute() -> None:
        local.run_development("root", inputs=SELECTION, parameters={"label": "a"})

    def revision(value: str | None) -> None:
        nonlocal local, events
        local, _, _, events, _, _ = location_graph(tmp_path, data_revision=value)

    revision(initial)
    execute()
    old = {row["value_key"] for row in database.rows["stored_results"]}
    events.clear()
    execute()
    assert events == []
    revision("second")
    execute()
    assert events == ["make", "a"]  # Unrelated source table remains cached.
    assert old <= {row["value_key"] for row in database.rows["stored_results"]}
    events.clear()
    execute()
    assert events == []
    revision(initial)
    execute()
    assert events == []


def test_runs_reuse_compiled_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = Mock(return_value=None)
    monkeypatch.setattr(computation, "installed_version", versions)
    runner, root, _, _, _ = setup_graph(tmp_path)
    runner.store = SQLiteStore(tmp_path / "values.sqlite")
    versions.reset_mock()
    runner.execute(root)
    first = runner.persistent_key(root.identity)
    assert runner.persistent_key(root.identity) == first
    runner.execute(root)
    versions.assert_not_called()
