"""Exercise ASV measurement adapters with isolated workloads."""

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from benchmarks import catalog, core, execution, interfaces, preparation, storage
from benchmarks.execution import CacheSuite
from benchmarks.interfaces import InterfaceSuite
from benchmarks.node_cache import NodeCacheSuite
from benchmarks.profiles import FAST_BENCH
from benchmarks.remote import ReceivingWorkerSuite
from benchmarks.results import ResultSuite
from lyra.runtime.result_store import ResultStore
from test_support.storage_workloads import storage_frame
from tests.sqlite_helpers import database

__all__ = ["database"]


@pytest.mark.parametrize("state", ["empty", "warm", "revision"])
def test_cache_states(state: str) -> None:
    suite = CacheSuite()
    suite.setup(state)
    try:
        suite.time_execute(state)
        assert bool(suite.work.calls) is (state != "warm")
        if suite.work.calls:
            assert suite.work.calls["source"] == 1
        suite.work.calls.clear()
        suite.time_execute(state)
        assert suite.work.calls == {}
    finally:
        suite.teardown(state)


@pytest.mark.usefixtures("database")
def test_interfaces_without_services() -> None:
    suite = InterfaceSuite()
    suite.setup(10)
    (Path(suite.temporary.name) / "results.sqlite3").touch()
    try:
        for name in dir(suite):
            if name.startswith("time_"):
                getattr(suite, name)(10)
    finally:
        suite.teardown(10)


@pytest.mark.usefixtures("database")
def test_worker_and_bounded_result() -> None:
    suite = ResultSuite()
    suite.setup(10)
    try:
        suite.time_worker(10)
        suite.time_preview(10)
        assert suite.fixture.work.calls["source"] == 1
        assert suite.track_preview_bytes(10) > 0
    finally:
        suite.teardown(10)


def test_receiving_worker_without_services() -> None:
    suite = ReceivingWorkerSuite()
    suite.setup(7)
    try:
        suite.time_execute(7)
        suite.peakmem_execute(7)
    finally:
        suite.teardown(7)


def test_fast_profile_selects_seven_cases() -> None:
    selected = []
    for module in (catalog, core, execution, interfaces, preparation, storage):
        for name, suite in vars(module).items():
            if not isinstance(suite, type) or suite.__module__ != module.__name__:
                continue
            for method in vars(suite):
                if not method.startswith("time_"):
                    continue
                for parameter in getattr(suite, "params", []):
                    prefix = module.__name__.split(".")[-1]
                    candidate = f"{prefix}.{name}.{method}({parameter!r})"
                    if re.fullmatch(FAST_BENCH, candidate):
                        selected.append(candidate)
    assert len(selected) == 7


def test_catalog_benchmark_serializes_every_family() -> None:
    suite = catalog.CatalogSuite()
    suite.setup(100)
    suite.time_discovery(100)
    summaries = suite.catalog.discovery.search(limit=100)
    assert len(summaries) == 100
    expected = json.dumps([item.model_dump(mode="json") for item in summaries]).encode()
    assert suite.track_catalog_bytes(100) == len(expected)


@pytest.mark.parametrize("case", ["typed-scalars", "mixed-geometries"])
def test_storage_adapters(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        storage, "storage_frame", lambda name: storage_frame(name, rows=12)
    )
    suite = storage.StorageSuite()
    suite.setup(case)
    for method in (
        suite.time_ownership,
        suite.time_to_rows,
        suite.time_from_rows,
        suite.time_roundtrip,
        suite.time_serialize,
    ):
        method(case)
    suite.peakmem_roundtrip(case)


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("graph", ["direct", "hybrid"])
@pytest.mark.parametrize("state", ["cold", "warm", "partial", "invalidated"])
def test_node_cache_workload(graph: str, state: str) -> None:
    suite = NodeCacheSuite()
    suite.setup(graph, state)
    try:
        result = suite.runner.run("root", inputs=suite.inputs)
        start = 50 if state == "partial" else 0
        assert set(result.tables["data"].location_id) == {
            str(i) for i in range(start, start + 100)
        }
        assert set(result.tables["data"].value) == {2 if graph == "hybrid" else 1}
        expected = (
            set()
            if state == "warm"
            else {str(i) for i in range(100, 150)}
            if state == "partial"
            else {str(i) for i in range(100)}
        )
        actual = set().union(*(ids for node, ids in suite.work.calls if node == "root"))
        assert actual == expected
        if graph == "hybrid":
            mapped = set().union(
                *(ids for node, ids in suite.work.calls if node == "related")
            )
            assert mapped == (expected if state != "invalidated" else set())
            contexts = [ids for node, ids in suite.work.calls if node == "context"]
            assert contexts == ([frozenset({"x", "y"})] if state == "cold" else [])
        # The fake database retains data in process; satisfy the public path check.
        suite.path.touch()
        with ResultStore.open(suite.path) as reader:
            restored = reader.read_result(result.metadata.result_ref)
        pd.testing.assert_frame_equal(
            restored.tables["data"]
            .sort_values(["location_id", "row_key"])
            .reset_index(drop=True),
            result.tables["data"]
            .sort_values(["location_id", "row_key"])
            .reset_index(drop=True),
        )
        suite.work.calls.clear()
        repeated = suite.runner.run("root", inputs=suite.inputs)
        assert suite.work.calls == []
        assert repeated.metadata.result_ref != result.metadata.result_ref
    finally:
        suite.teardown(graph, state)
