"""Persistent reuse ordering and isolation from storage failures."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from typing_extensions import override

from lyra.contracts.errors import MetricResultError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.contracts.resources import IOConfig
from lyra.data.locations import (
    Locations,  # ruff: ignore[typing-only-first-party-import]
)
from lyra.runtime.cache import CachedArtifact, TableCache
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning import computation
from lyra.runtime.planning.catalog import compile_catalog
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.sdk.resources import BaseIO, Resource
from test_support.location_helpers import node_contract, producer_result
from test_support.location_resolution import SELECTED
from test_support.location_schema import contract, declaration, locations
from tests.cache_helpers import SCHEMA, cached_value
from tests.graph_execution_helpers import Label, executor
from tests.location_cache_graph import setup_graph as location_graph

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping

    import geopandas as gpd

    from lyra.data.results import NativeOutputs
    from lyra.runtime.arguments import PreparedResolvedInvocation


class RecordingCache(TableCache):
    def __init__(self, directory: Path, events: list[str]) -> None:
        super().__init__(directory)
        self.events = events
        self.active: str | None = None
        self.names: dict[str, str] = {}
        self.arriving: NativeOutputs | None = None

    @contextmanager
    def lock(self, key: str) -> Generator[bool, None, None]:
        assert self.active is None, "nested invocation lock"
        name = self.names[key]
        self.events.append(f"lock:{name}")
        with super().lock(key) as acquired:
            self.active = key
            try:
                if self.arriving is not None and name == "consumer":
                    super().write(
                        key, self.arriving, dict.fromkeys(self.arriving, SCHEMA)
                    )
                yield acquired
            finally:
                self.active = None
                self.events.append(f"unlock:{name}")

    def read(
        self, key: str, validate: Callable[[NativeOutputs], NativeOutputs] | None = None
    ) -> CachedArtifact[NativeOutputs] | None:
        self.events.append(f"read:{self.names[key]}")
        return super().read(key, validate)

    def write(
        self,
        key: str,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> str | None:
        self.events.append(f"write:{self.names[key]}")
        return super().write(key, outputs, schemas)


def setup_graph(
    tmp_path: Path,
) -> tuple[
    GraphExecutor, PreparedResolvedInvocation, RecordingCache, list[str], list[bool]
]:
    events: list[str] = []
    broken = [False]

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [1]})

    @metric(
        name="consumer",
        description="Consumer",
        outputs={"consumer": SCHEMA},
        inputs={"table": Dependency("source", producer="source")},
    )
    def consumer(table: pd.DataFrame) -> pd.DataFrame:
        events.append("consumer")
        return pd.DataFrame({"wrong": [1]}) if broken[0] else table

    runner, plugin = executor(consumer, source)
    cache = RecordingCache(tmp_path, events)
    runner.cache = cache
    root = plugin.prepare_resolved_invocation("consumer", {})
    source_input = runner.plugins["plugin_1"].prepare_resolved_invocation("source", {})
    cache.names = {
        runner.persistent_key(root.identity): "consumer",
        runner.persistent_key(source_input.identity): "source",
    }
    return runner, root, cache, events, broken


def test_dependencies_finish_before_lock_and_first_hit_skips_work(
    tmp_path: Path,
) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    runner.execute(root)
    assert events == [
        "read:consumer",
        "read:source",
        "lock:source",
        "read:source",
        "source",
        "write:source",
        "unlock:source",
        "lock:consumer",
        "read:consumer",
        "consumer",
        "write:consumer",
        "unlock:consumer",
    ]
    events.clear()
    runner.context_factory = Mock(side_effect=AssertionError("context acquired"))
    runner.execute(root)
    assert events == ["read:consumer"]
    assert cache.active is None


def test_locked_recheck_uses_entry_published_by_another_writer(tmp_path: Path) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    cache.arriving = {"consumer": pd.DataFrame({"value": [7]})}
    assert runner.execute(root).outputs["consumer"].value.tolist() == [7]
    assert "source" in events
    assert "consumer" not in events
    assert "write:consumer" not in events


@pytest.mark.parametrize(
    "failure", ["read", "lock", "acquire", "write", "lookup", "unlock"]
)
def test_storage_failures_do_not_fail_calculation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    error = OSError("storage unavailable")
    if failure == "read":
        cache.write(
            runner.persistent_key(root.identity),
            {"consumer": pd.DataFrame({"value": [7]})},
            {"consumer": SCHEMA},
        )
        monkeypatch.setattr(cache.store, "read", Mock(side_effect=error))
    elif failure == "write":
        monkeypatch.setattr(cache.store, "write", Mock(side_effect=error))
    elif failure == "lookup":
        monkeypatch.setattr(Path, "replace", Mock(side_effect=error))
    else:
        lock = Mock()
        if failure == "acquire":
            lock.acquire.side_effect = error
        elif failure == "unlock":
            lock.release.side_effect = error
        factory = (
            Mock(side_effect=error) if failure == "lock" else Mock(return_value=lock)
        )
        monkeypatch.setattr("lyra.runtime.cache.FileLock", factory)
    events.clear()
    outcome = runner.execute(root)
    assert outcome.outputs["consumer"].value.tolist() == [1]
    assert events.count("consumer") == events.count("source") == 1
    if failure in {"lock", "acquire"}:
        assert not any(event.startswith("write:") for event in events)
    if failure == "lookup":
        assert outcome.artifact_id is not None
        assert cache.store.read(outcome.artifact_id)["consumer"].value.tolist() == [1]
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert warnings
    assert all(record.name == "lyra.runtime.cache" for record in warnings)


@pytest.mark.parametrize("corrupt", [False, True])
def test_invalid_cache_recomputes(tmp_path: Path, *, corrupt: bool) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    key = runner.persistent_key(root.identity)
    with cache.lock(key):
        cache.write(
            key,
            {"consumer": pd.DataFrame({"value": ["invalid"]})},
            {"consumer": pa.DataFrameSchema({"value": pa.Column(str)})},
        )
    if corrupt:
        (
            cache.store.directory / str(cache.reference(key)) / "manifest.json"
        ).write_text("broken")
    events.clear()
    assert runner.execute(root).outputs["consumer"].value.tolist() == [1]
    assert "consumer" in events
    events.clear()
    runner.execute(root)
    assert events == ["read:consumer"]


@pytest.mark.parametrize("storage_failure", [None, "unlock"])
def test_invalid_handler_is_not_published_and_dependencies_survive(
    tmp_path: Path,
    storage_failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, root, cache, events, broken = setup_graph(tmp_path)
    broken[0] = True
    if storage_failure == "unlock":
        lock = Mock(release=Mock(side_effect=OSError("unlock unavailable")))
        monkeypatch.setattr("lyra.runtime.cache.FileLock", Mock(return_value=lock))
    with pytest.raises(GraphExecutionError) as error:
        runner.execute(root)
    assert isinstance(error.value.__cause__, MetricResultError)
    assert "write:consumer" not in events
    assert cached_value(cache.read(runner.persistent_key(root.identity))) is None
    events.clear()
    broken[0] = False
    runner.execute(root)
    assert "source" not in events
    assert "consumer" in events


@pytest.mark.parametrize("revision", [None, "changed"])
def test_location_preparation_runs_before_persistent_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str | None
) -> None:
    events = []

    @location_operation(
        output_contract=node_contract("test:units", "lot", contract(declaration())),
        name="make",
        description="Make",
        output=contract(declaration()),
    )
    def make() -> gpd.GeoDataFrame:
        events.append("prepare")
        return producer_result(locations({}), contract(declaration()))

    @metric(name="support", description="Support", outputs={"support": SCHEMA})
    def support() -> pd.DataFrame:
        events.append("support")
        return pd.DataFrame({"value": [1]})

    @metric(
        locations={
            "location": node_contract("test:units", "lot", contract(declaration()))
        },
        name="consume",
        description="Consume",
        outputs={"consume": SCHEMA},
        inputs={"table": Dependency("support", producer="support")},
    )
    def consume(location: Locations, table: pd.DataFrame) -> pd.DataFrame:
        assert location.ids == ["0"]
        events.append("consume")
        return table

    @metric(
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "table": Dependency(
                "consume",
                locations={"location": LocationDependency("make")},
                producer="consume",
            )
        },
    )
    def root(parameters: Label, table: pd.DataFrame) -> pd.DataFrame:
        assert parameters.label in {"a", "b"}
        return table

    plugin = PluginDefinition(
        metrics=[root, consume, support], location_operations=[make]
    )
    runner = GraphExecutor(
        LocalRunner.from_definitions({"p": plugin}).catalog,
        {"p": BoundPlugin(plugin)},
        Mock(),
        cache=TableCache(tmp_path),
    )
    runner.execute(
        runner.plugins["p"].prepare_resolved_invocation(
            "root", {"parameters": {"label": "a"}}
        )
    )
    assert events == ["prepare", "support", "consume"]
    events.clear()
    read_locations = TableCache.read_locations

    def read(
        cache: TableCache,
        key: str,
        validate: Callable[[Locations], Locations] | None = None,
    ) -> CachedArtifact[Locations] | None:
        events.append("read:make")
        return read_locations(cache, key, validate)

    monkeypatch.setattr(TableCache, "read_locations", read)
    runner.cache_revision = revision
    runner.execute(
        runner.plugins["p"].prepare_resolved_invocation(
            "root", {"parameters": {"label": "b"}}
        )
    )
    assert events == (
        ["read:make"]
        if revision is None
        else ["read:make", "read:make", "prepare", "support", "consume"]
    )


def test_first_read_hit_skips_dependency_resources(tmp_path: Path) -> None:
    acquisitions = []

    class Support(BaseIO[IOConfig]):
        @override
        def setup(self) -> None:
            acquisitions.append("setup")

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source(support: Annotated[BaseIO, Resource("support")]) -> pd.DataFrame:
        assert isinstance(support, Support)
        return pd.DataFrame({"value": [1]})

    @metric(
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
        cache=TableCache(tmp_path),
    )
    invocation = runner.plugins["p"].prepare_resolved_invocation("root", {})
    runner.execute(invocation)
    assert acquisitions == ["setup"]
    acquisitions.clear()
    runner.execute(invocation)
    assert acquisitions == []


@pytest.mark.parametrize("revision", [None, "first"])
def test_revision_namespaces_preserve_old_entries(
    tmp_path: Path, revision: str | None
) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    runner.cache_revision = revision
    cache.names = {
        runner.persistent_key(root.identity): "consumer",
        runner.persistent_key(
            runner.plugins["plugin_1"]
            .prepare_resolved_invocation("source", {})
            .identity
        ): "source",
    }
    runner.execute(root)
    old_key = runner.persistent_key(root.identity)
    runner.cache_revision = "second"
    cache.names.update(
        {
            runner.persistent_key(root.identity): "consumer",
            runner.persistent_key(
                runner.plugins["plugin_1"]
                .prepare_resolved_invocation("source", {})
                .identity
            ): "source",
        }
    )
    events.clear()
    runner.execute(root)
    assert "source" in events
    assert "consumer" in events
    assert (tmp_path / "cache" / old_key).is_file()
    runner.cache_revision = revision
    events.clear()
    runner.execute(root)
    assert events == ["read:consumer"]


def test_namespace_contract_versions_and_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(computation, "installed_version", lambda _: None)
    runner, root, _, _, _ = setup_graph(tmp_path)
    original = runner.persistent_key(root.identity)
    manifests = dict(runner.catalog.manifests)
    runner.catalog = compile_catalog(dict(reversed(list(manifests.items()))))
    assert runner.persistent_key(root.identity) == original
    for manifest in manifests.values():
        for node in manifest.nodes:
            node.description = "Edited description"
    runner.catalog = compile_catalog(manifests)
    assert runner.persistent_key(root.identity) == original
    monkeypatch.setattr(computation, "installed_version", lambda _: "2")
    assert runner.persistent_key(root.identity) == original  # Captured versions.
    runner.catalog = compile_catalog(manifests)
    assert runner.persistent_key(root.identity) != original
    monkeypatch.setattr(computation, "installed_version", lambda _: None)
    monkeypatch.setitem(
        computation.PROTOCOLS, "artifacts", computation.PROTOCOLS["artifacts"] + 1
    )
    runner.catalog = compile_catalog(manifests)
    assert runner.persistent_key(root.identity) != original


@pytest.mark.parametrize(
    "change", ["provider_contract", "provider_version", "producer_version"]
)
def test_only_participating_versions_invalidate_seeded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    versions: dict[str, str] = {}
    monkeypatch.setattr(computation, "installed_version", versions.get)
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.cache = TableCache(tmp_path)
    manifests = dict(runner.catalog.manifests)
    manifests[SELECTED.distribution] = PluginManifest(
        plugin=PluginInfo(name=SELECTED.distribution, version="test"),
        factory="test:create",
        location_provider=SELECTED.declaration,
        resources={SELECTED.resource: {}, "other": {}},
    )
    runner.catalog = compile_catalog(manifests)
    runner.execute(root)
    events.clear()
    runner.execute(root)
    assert events == []
    if change == "provider_contract":
        manifests[
            SELECTED.distribution
        ].location_provider = SELECTED.declaration.model_copy(
            update={"resource": "other"}
        )
    else:
        versions[
            SELECTED.distribution if change == "provider_version" else "plugin_1"
        ] = "2"
    runner.catalog = compile_catalog(manifests)
    runner.execute(root)
    assert events == (["source", "consumer"] if change == "producer_version" else [])


@pytest.mark.parametrize("revision", [None, "first"])
def test_location_revision_preserves_reuse(
    tmp_path: Path, revision: str | None
) -> None:
    events: list[str] = []

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [1]})

    @location_operation(
        output_contract=node_contract("test:units", "lot", contract(declaration())),
        name="make",
        description="Make",
        output=contract(declaration()),
        inputs={"table": Dependency("source", producer="source")},
    )
    def make(table: pd.DataFrame) -> gpd.GeoDataFrame:
        events.append("make")
        assert table.value.tolist() == [1]
        return producer_result(locations({}), contract(declaration()))

    @metric(
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "table": Dependency(
                "consume",
                locations={"location": LocationDependency("make")},
                producer="consume",
            )
        },
    )
    def root_metric(table: pd.DataFrame) -> pd.DataFrame:
        return table

    @metric(
        locations={
            "location": node_contract("test:units", "lot", contract(declaration()))
        },
        name="consume",
        description="Consume",
        outputs={"consume": SCHEMA},
    )
    def consume(location: Locations) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(location.ids)]})

    plugin = PluginDefinition(
        metrics=[source, root_metric, consume], location_operations=[make]
    )
    runner = GraphExecutor(
        LocalRunner.from_definitions({"p": plugin}).catalog,
        {"p": BoundPlugin(plugin)},
        Mock(),
        cache=TableCache(tmp_path),
        cache_revision=revision,
    )
    root = runner.plugins["p"].prepare_resolved_invocation("root", {})
    runner.execute(root)
    old_key = runner.persistent_key(root.identity)
    events.clear()
    runner.execute(root)
    assert events == []
    runner.cache_revision = "second"
    runner.execute(root)
    assert events == ["source", "make"]
    assert (tmp_path / "cache" / old_key).is_file()
    events.clear()
    runner.cache_revision = revision
    runner.execute(root)
    assert events == []


def test_wrong_output_name_recomputes(tmp_path: Path) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    key = runner.persistent_key(root.identity)
    with cache.lock(key):
        cache.write(
            key, {"unexpected": pd.DataFrame({"value": [9]})}, {"unexpected": SCHEMA}
        )
    events.clear()
    assert runner.execute(root).outputs["consumer"].value.tolist() == [1]
    assert "consumer" in events


def test_dependency_edges_keep_native_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, root, _, events, _ = setup_graph(tmp_path)
    runner.cache = None
    conversion = Mock(side_effect=AssertionError("Unexpected storage conversion"))
    hashing = Mock(side_effect=AssertionError("Unexpected content hashing"))
    monkeypatch.setattr("lyra.data.arrow_conversion.to_arrow", conversion)
    monkeypatch.setattr("lyra.data.content_fingerprints.typed_snapshot", hashing)
    assert runner.execute(root).outputs["consumer"].value.tolist() == [1]
    assert events.count("source") == 1
    conversion.assert_not_called()
    hashing.assert_not_called()


@pytest.mark.parametrize("initial", [None, "first"])
def test_target_revision_invalidates_transitive_cache(
    tmp_path: Path, initial: str | None
) -> None:
    local, _, _, events, _, _ = location_graph(tmp_path)
    graph = local.catalog.graph

    def execute() -> None:
        local.run_development("root", {"parameters": {"label": "a"}})

    def revision(value: str | None) -> None:
        for entry in graph.nodes.values():
            for binding in (
                value.contract for value in entry.node.location_inputs.values()
            ):
                binding.data_revision = value
            if entry.node.kind == "location_operation":
                entry.node.output_contract.data_revision = value
        manifests = dict(local.catalog.manifests)
        for distribution, manifest in manifests.items():
            manifest.nodes = [
                entry.node
                for entry in graph.nodes.values()
                if entry.distribution == distribution
            ]
        local.catalog = compile_catalog(manifests)

    revision(initial)
    execute()
    old = set((tmp_path / "bundles").iterdir())
    events.clear()
    execute()
    assert events == []
    revision("second")
    execute()
    assert events == ["make", "a"]  # Unrelated source table remains cached.
    assert all(path.is_dir() for path in old)
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
    runner.cache = TableCache(tmp_path)
    versions.reset_mock()
    runner.execute(root)
    first = runner.persistent_key(root.identity)
    runner.cache_revision = "fresh"
    assert runner.persistent_key(root.identity) != first
    runner.execute(root)
    versions.assert_not_called()
