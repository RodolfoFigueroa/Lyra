"""Exercise ASV measurement adapters with isolated workloads."""

import json
import re

import pytest

from benchmarks import catalog, core, execution, interfaces, preparation
from benchmarks.execution import CacheSuite
from benchmarks.interfaces import InterfaceSuite
from benchmarks.profiles import FAST_BENCH
from benchmarks.results import ResultSuite
from test_support.workloads import installed, workload


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
        assert suite.fixture.work.calls["source"] == 1
        assert suite.track_preview_bytes(10) > 0
    finally:
        suite.teardown(10)


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
