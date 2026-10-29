"""Persistent reuse ordering and isolation from storage failures."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import (
    BaseIO,
    Dependency,
    IOConfig,
    LocationDependency,
    Locations,
    PluginDefinition,
    Resource,
    location_operation,
    metric,
)
from lyra.sdk.cache import TableCache
from lyra.sdk.errors import MetricResultError
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import build_graph
from typing_extensions import override

from tests.test_graph_execution import Label, executor
from tests.test_location_schema import contract, declaration, locations
from tests.test_table_cache import SCHEMA

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from lyra.sdk.arguments import PreparedInvocation
    from lyra.sdk.results import NativeOutputs


class RecordingCache(TableCache):
    def __init__(self, directory: Path, events: list[str]) -> None:
        super().__init__(directory)
        self.events = events
        self.active: str | None = None
        self.names: dict[str, str] = {}
        self.failure: str | None = None
        self.arriving: NativeOutputs | None = None

    @contextmanager
    def lock(self, key: str) -> Iterator[bool]:
        assert self.active is None, "nested invocation lock"
        name = self.names[key]
        self.events.append(f"lock:{name}")
        if self.failure == "lock":
            message = "lock unavailable"
            raise RuntimeError(message)
        with super().lock(key) as acquired:
            self.active = key
            try:
                if self.arriving is not None and name == "consumer":
                    super().write(key, self.arriving)
                yield acquired and self.failure != "unavailable"
            finally:
                self.active = None
                self.events.append(f"unlock:{name}")
        if self.failure == "unlock":
            message = "unlock unavailable"
            raise RuntimeError(message)

    def read(self, key: str) -> NativeOutputs | None:
        self.events.append(f"read:{self.names[key]}")
        if self.failure == "read":
            message = "read unavailable"
            raise RuntimeError(message)
        return super().read(key)

    def write(self, key: str, outputs: NativeOutputs) -> None:
        self.events.append(f"write:{self.names[key]}")
        if self.failure == "write":
            message = "serialization failed"
            raise ValueError(message)
        super().write(key, outputs)


def setup_graph(
    tmp_path: Path,
) -> tuple[GraphExecutor, PreparedInvocation, RecordingCache, list[str], list[bool]]:
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
        inputs={"table": Dependency("source")},
    )
    def consumer(table: pd.DataFrame) -> pd.DataFrame:
        events.append("consumer")
        return pd.DataFrame({"wrong": [1]}) if broken[0] else table

    runner, plugin = executor(consumer, source)
    cache = RecordingCache(tmp_path, events)
    runner.cache = cache
    root = plugin.prepare_invocation("consumer", {})
    source_input = runner.plugins["plugin_1"].prepare_invocation("source", {})
    cache.names = {
        cache.key(root.identity): "consumer",
        cache.key(source_input.identity): "source",
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
    assert runner.execute(root)["consumer"].value.tolist() == [7]
    assert "source" in events
    assert "consumer" not in events
    assert "write:consumer" not in events


@pytest.mark.parametrize("failure", ["read", "lock", "unavailable", "write", "unlock"])
def test_storage_failures_do_not_fail_calculation(tmp_path: Path, failure: str) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    cache.failure = failure
    assert runner.execute(root)["consumer"].value.tolist() == [1]
    assert events.count("consumer") == events.count("source") == 1
    if failure in {"lock", "unavailable"}:
        assert not any(event.startswith("write:") for event in events)


@pytest.mark.parametrize("corrupt", [False, True])
def test_invalid_cache_recomputes(tmp_path: Path, *, corrupt: bool) -> None:
    runner, root, cache, events, _ = setup_graph(tmp_path)
    key = cache.key(root.identity)
    with cache.lock(key):
        cache.write(key, {"consumer": pd.DataFrame({"value": ["invalid"]})})
    if corrupt:
        (tmp_path / key / "manifest.json").write_text("broken")
    events.clear()
    assert runner.execute(root)["consumer"].value.tolist() == [1]
    assert "consumer" in events
    events.clear()
    runner.execute(root)
    assert events == ["read:consumer"]


@pytest.mark.parametrize("storage_failure", [None, "unlock"])
def test_invalid_handler_is_not_published_and_dependencies_survive(
    tmp_path: Path,
    storage_failure: str | None,
) -> None:
    runner, root, cache, events, broken = setup_graph(tmp_path)
    broken[0] = True
    cache.failure = storage_failure
    with pytest.raises(GraphExecutionError) as error:
        runner.execute(root)
    assert isinstance(error.value.__cause__, MetricResultError)
    assert "write:consumer" not in events
    assert cache.read(cache.key(root.identity)) is None
    events.clear()
    broken[0] = False
    runner.execute(root)
    assert "source" not in events
    assert "consumer" in events


def test_location_preparation_runs_before_persistent_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []

    @location_operation(name="make", description="Make", output=contract(declaration()))
    def make() -> Locations:
        events.append("prepare")
        return locations({})

    @metric(name="support", description="Support", outputs={"support": SCHEMA})
    def support() -> pd.DataFrame:
        events.append("support")
        return pd.DataFrame({"value": [1]})

    @metric(
        name="consume",
        description="Consume",
        outputs={"consume": SCHEMA},
        inputs={"table": Dependency("support")},
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
                "consume", locations={"location": LocationDependency("make")}
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
        build_graph({"p": plugin.node_manifests}),
        {"p": plugin},
        Mock(),
        cache=TableCache(tmp_path),
    )
    runner.execute(plugin.prepare_invocation("root", {"parameters": {"label": "a"}}))
    assert events == ["prepare", "support", "consume"]
    events.clear()
    read_locations = TableCache.read_locations

    def read(cache: TableCache, key: str) -> Locations | None:
        events.append("read:make")
        return read_locations(cache, key)

    monkeypatch.setattr(TableCache, "read_locations", read)
    runner.execute(plugin.prepare_invocation("root", {"parameters": {"label": "b"}}))
    assert events == ["read:make"]


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
        inputs={"table": Dependency("source")},
    )
    def root(table: pd.DataFrame) -> pd.DataFrame:
        return table

    plugin = PluginDefinition(metrics=[source, root], resources={"support": Support})
    runner = GraphExecutor(
        build_graph({"p": plugin.node_manifests}),
        {"p": plugin},
        Mock(),
        cache=TableCache(tmp_path),
    )
    invocation = plugin.prepare_invocation("root", {})
    runner.execute(invocation)
    assert acquisitions == ["setup"]
    acquisitions.clear()
    runner.execute(invocation)
    assert acquisitions == []
