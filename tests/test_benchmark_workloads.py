"""Correctness and isolation of the permanent performance workloads."""

import json
import re

import pandas as pd
import pytest
from lyra.sdk import LocalRunner, ipc_codec
from lyra.sdk.preparation import prepare_execution
from lyra.sdk.typed_storage import Snapshot

from benchmarks import catalog, core, execution, interfaces, preparation
from benchmarks.execution import CacheSuite
from benchmarks.interfaces import InterfaceSuite
from benchmarks.locations import LocationExecutionSuite
from benchmarks.preparation import PreparationSuite
from benchmarks.profiles import FAST_BENCH
from benchmarks.results import ResultSuite
from benchmarks.workloads import frame, installed, schema, workload


@pytest.mark.parametrize(("shape", "increment"), [("chain", 6), ("diamond", 4)])
def test_repeated_graph_execution(shape: str, increment: int) -> None:
    work = workload(7, shape)
    runner = LocalRunner.from_definitions({"benchmark-fixture": work.plugin})
    expected = frame(7)
    expected["value"] = (
        expected.value + increment
        if shape == "chain"
        else expected.value * 4 + increment
    )
    for run in range(2):
        pd.testing.assert_frame_equal(runner.run("root", {})["root"], expected)
        assert work.calls["source"] == run + 1


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


def test_preparation_without_provider() -> None:
    suite = PreparationSuite()
    suite.setup(3)
    prepared = prepare_execution(suite.catalog, suite.request)
    assert [item.name for item in prepared.inputs] == ["left", "right"]
    assert prepared.inputs[0].ids == ("0", "1", "2")
    suite.time_reject(3)


def test_interfaces_without_services() -> None:
    suite = InterfaceSuite()
    declarations = suite.setup_cache()
    suite.setup(declarations, 10)
    try:
        for name in dir(suite):
            if name.startswith("time_"):
                getattr(suite, name)(declarations, 10)
    finally:
        suite.teardown(declarations, 10)


def test_worker_and_bounded_result() -> None:
    suite = ResultSuite()
    suite.setup(10)
    try:
        suite.time_worker(10)
        suite.time_preview(10)
        assert suite.work.calls["source"] == 1
        assert suite.track_preview_bytes(10) > 0
    finally:
        suite.teardown(10)


def test_location_execution() -> None:
    suite = LocationExecutionSuite()
    suite.setup(3)
    result = suite.runner.run("root", suite.inputs)
    pd.testing.assert_frame_equal(result["root"], frame(6))


@pytest.mark.parametrize("kind", ["mixed", "polygon"])
def test_storage_roundtrip_and_fingerprints(kind: str) -> None:
    table = frame(17, kind)
    snapshot = Snapshot.from_frame(table, schema(table))
    first = snapshot.fingerprints
    assert snapshot.fingerprints is first
    restored = ipc_codec.decode(ipc_codec.encode(snapshot))
    assert restored.fingerprints == first


def test_fast_profile_selects_seven_cases() -> None:
    selected = []
    for module in (catalog, core, execution, interfaces, preparation):
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
    declarations = {100: installed(workload(1, "catalog", 100).plugin)}
    suite.setup(declarations, 100)
    suite.time_discovery(declarations, 100)
    summaries = suite.catalog.discovery.search(limit=100)
    assert len(summaries) == 100
    expected = json.dumps([item.model_dump(mode="json") for item in summaries]).encode()
    assert suite.track_catalog_bytes(declarations, 100) == len(expected)
