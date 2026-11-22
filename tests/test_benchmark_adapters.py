"""Exercise ASV measurement adapters with isolated workloads."""

import json
import pickle  # ruff: ignore[suspicious-pickle-import] - ASV serializes setup caches with pickle.
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from asv_runner.benchmarks.peakmem import PeakMemBenchmark
from asv_runner.benchmarks.time import TimeBenchmark

from benchmarks import catalog, core, execution, interfaces, preparation, storage
from benchmarks import retained_input_scaling as adapters
from benchmarks import retained_inputs as complements
from benchmarks.execution import CacheSuite
from benchmarks.interfaces import InterfaceSuite
from benchmarks.node_cache import NodeCacheSuite
from benchmarks.profiles import (
    FAST_BENCH,
    RETAINED_INPUT_BENCH,
    RETAINED_INPUT_SCALING_BENCH,
)
from benchmarks.remote import ReceivingWorkerSuite
from benchmarks.results import ResultSuite
from lyra.runtime.result_store import ResultStore
from test_support import retained_benchmarks as support
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
    # Two retained and two fresh partitions exercise partial assembly.
    suite.partition_count = 4
    suite.setup(graph, state)
    try:
        result = suite.runner.run("root", inputs=suite.inputs)
        start = 2 if state == "partial" else 0
        assert set(result.tables["data"].location_id) == {
            str(i) for i in range(start, start + 4)
        }
        assert set(result.tables["data"].value) == {2 if graph == "hybrid" else 1}
        expected = (
            set()
            if state == "warm"
            else {"4", "5"}
            if state == "partial"
            else {"0", "1", "2", "3"}
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


SUITES = (
    adapters.SmallInputSuite,
    adapters.LargeInputSuite,
    adapters.ColdInputSuite,
    adapters.MixedInputSuite,
    adapters.SmallMixedInputSuite,
    adapters.InputMemorySuite,
    complements.ReadSuite,
    complements.InvalidationSuite,
    complements.CombinedSuite,
    complements.ColdMemorySuite,
    complements.ReadWriteMemorySuite,
    complements.MixedMemorySuite,
)
PARAMETERS = [(suite, name) for suite in SUITES for name in suite.params]


def test_catalog_and_selectors() -> None:
    assert sum(len(s.params) for s in SUITES if hasattr(s, "time_operation")) == 46
    assert sum(len(s.params) for s in SUITES if hasattr(s, "peakmem_operation")) == 18
    assert (
        2
        * (
            len(complements.SmallStorageSuite.params)
            + len(complements.LargeStorageSuite.params)
        )
        == 30
    )
    assert len(adapters.InputMemorySuite.params) == 9
    for suite, name in PARAMETERS:
        case = support.CASES[name]
        assert (
            case.fixture.references == 0
            or case.fixture.graph == "direct"
            or case.fixture.partitions == 100
        )
        module = suite.__module__.removeprefix("benchmarks.")
        method = (
            "time_operation"
            if hasattr(suite, "time_operation")
            else "peakmem_operation"
        )
        benchmark = f"{module}.{suite.__name__}.{method}({name!r})"
        assert re.match(RETAINED_INPUT_BENCH, benchmark)
        assert bool(re.match(RETAINED_INPUT_SCALING_BENCH, benchmark)) == (
            module == "retained_input_scaling"
        )
    assert re.match(RETAINED_INPUT_BENCH, "retained_inputs.ReadSuite.time_operation")
    assert not re.match(
        RETAINED_INPUT_SCALING_BENCH, "retained_inputs.ReadSuite.time_operation"
    )


def test_seed_cache_groups() -> None:
    # Shared cache hooks avoid rebuilding the same states for different metrics.
    def cache_key(suite: type) -> str:
        benchmark = PeakMemBenchmark(
            "inputs.peakmem_operation", lambda: None, [suite()]
        )
        return benchmark.setup_cache_key

    complete = cache_key(adapters.LargeInputSuite)
    assert complete == cache_key(adapters.InputMemorySuite)
    assert complete == cache_key(adapters.MixedInputSuite)
    assert complete == cache_key(complements.LargeStorageSuite)
    assert (
        len(
            {
                complete,
                cache_key(adapters.SmallInputSuite),
                cache_key(complements.CombinedSuite),
            }
        )
        == 3
    )
    assert not hasattr(adapters.ColdInputSuite, "setup_cache")
    assert not hasattr(complements.ColdMemorySuite, "setup_cache")
    for suite in (
        adapters.SmallMixedInputSuite,
        complements.MixedMemorySuite,
        complements.InvalidationSuite,
    ):
        assert cache_key(suite) == cache_key(adapters.SmallInputSuite)


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize(("suite_type", "name"), PARAMETERS)
def test_all_adapters_with_isolated_database(
    suite_type: type[support.Sample],
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = support.CASES[name]
    fixture = replace(
        case.fixture, partitions=10, references=100 if case.fixture.references else 0
    )
    monkeypatch.setitem(support.CASES, name, replace(case, fixture=fixture))
    suite = suite_type()
    if case.operation == "cold":
        suite.begin(name)
    else:
        seed = support.prepare_seed(fixture, tmp_path, mixed=case.operation == "mixed")
        Path(seed.path).touch()  # The statement fake stores rows in memory.
        suite.begin(
            name, {fixture.name + ("-mixed" if case.operation == "mixed" else ""): seed}
        )
    path = suite.path
    try:
        suite.execute()
        suite.validate()
    finally:
        suite.teardown()
    assert not path.exists()


@pytest.mark.parametrize(
    ("hook", "partitions", "storage"),
    [
        (support.small_seeds, 100, True),
        (support.large_seeds, 1000, True),
        (support.combined_seeds, 100, False),
    ],
)
def test_seed_selection(
    hook: Callable, partitions: int, storage: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare = Mock(return_value={})
    monkeypatch.setattr(support, "prepare_seeds", prepare)
    hook()
    assert {f.partitions for f in prepare.call_args.args[0]} == {partitions}
    assert prepare.call_args.kwargs.get("storage", True) is storage


def test_manifest_and_disposable_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = support.SMALL[0]
    seed_path = tmp_path / "seed.sqlite"
    seed_path.write_bytes(b"immutable")
    manifest = {fixture.name: support.Seed(str(seed_path), "reference", 100)}
    encoded = json.dumps({key: asdict(seed) for key, seed in manifest.items()})
    assert {
        key: support.Seed(**value) for key, value in json.loads(encoded).items()
    } == manifest
    monkeypatch.setattr(support.LocalRunner, "from_definitions", Mock())
    for _ in range(2):
        sample = adapters.SmallInputSuite()
        sample.setup(manifest, support.Case(fixture, "cached").name)
        assert sample.path.read_bytes() == b"immutable"
        sample.path.write_bytes(b"publication")
        sample.teardown()
        assert not sample.path.exists()
    assert seed_path.read_bytes() == b"immutable"


@pytest.mark.parametrize("failure", ["copy", "runner", "operation", "validation"])
def test_failure_cleanup(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = support.SMALL[0]
    path = tmp_path / "seed"
    path.touch()
    manifest = {fixture.name: support.Seed(str(path), "reference", 100)}
    sample = adapters.SmallInputSuite()
    error = ValueError("failure")
    monkeypatch.setattr(support.LocalRunner, "from_definitions", Mock())
    if failure == "copy":
        monkeypatch.setattr(support.shutil, "copyfile", Mock(side_effect=error))
    elif failure == "runner":
        monkeypatch.setattr(
            support.LocalRunner, "from_definitions", Mock(side_effect=error)
        )

    def exercise() -> None:
        sample.setup(manifest, support.Case(fixture, "cached").name)
        if failure == "operation":
            monkeypatch.setattr(sample, "operation", Mock(side_effect=error))
            sample.execute()
        elif failure == "validation":
            sample.completed = True
            monkeypatch.setattr(sample, "validate", Mock(side_effect=error))
            sample.teardown()

    with pytest.raises(ValueError, match="failure"):
        exercise()
    assert sample.directory is None
    assert not sample.path.exists()
    assert path.exists()


def test_seed_failure_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    error = ValueError("seed failure")
    monkeypatch.setattr(support, "prepare_seed", Mock(side_effect=error))
    with pytest.raises(ValueError, match="seed failure"):
        support.small_seeds()
    assert not (tmp_path / "seeds").exists()


def test_memory_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    suite = adapters.InputMemorySuite()
    monkeypatch.setattr(suite, "operation", lambda: events.append("operation"))
    monkeypatch.setattr(suite, "validate", lambda: events.append("validation"))
    monkeypatch.setattr(
        "asv_runner.benchmarks.peakmem.get_maxrss", lambda: events.append("peak") or 123
    )
    benchmark = PeakMemBenchmark(
        "inputs.peakmem_operation", suite.peakmem_operation, [suite]
    )
    assert benchmark.run({}, "case") == 123
    assert events == ["operation", "peak"]
    suite.teardown()
    assert events == ["operation", "peak", "validation"]


def test_memory_setup_never_produces_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = support.MEMORY_CASES[0]
    path = tmp_path / "closed.sqlite"
    path.write_bytes(b"seed")
    seed = support.Seed(str(path), "reference", case.fixture.partitions)
    runner = Mock()
    monkeypatch.setattr(
        support.LocalRunner, "from_definitions", Mock(return_value=runner)
    )
    forbidden = Mock(
        side_effect=AssertionError("Seed production in measurement process")
    )
    monkeypatch.setattr(support, "prepare_seeds", forbidden)
    suite = adapters.InputMemorySuite()
    suite.setup({case.fixture.name: seed}, case.name)
    try:
        assert suite.path.read_bytes() == b"seed"
        runner.run.assert_not_called()
        forbidden.assert_not_called()
    finally:
        suite.teardown()


def test_wrong_seed_state_is_rejected(tmp_path: Path) -> None:
    case = support.MIXED_CASES[0]
    path = tmp_path / "seed"
    path.touch()
    seed = support.Seed(str(path), "reference", case.fixture.partitions)
    suite = adapters.MixedInputSuite()
    with pytest.raises(AssertionError):
        suite.setup({case.fixture.name + "-mixed": seed}, case.name)
    assert suite.directory is None
    assert not suite.path.exists()


@pytest.mark.parametrize("suite", [s for s in SUITES if hasattr(s, "time_operation")])
def test_retained_sampling_budget(suite: type[support.Sample]) -> None:
    benchmark = TimeBenchmark("inputs.time_operation", lambda: None, [suite()])
    assert benchmark.rounds == 2
    assert benchmark.repeat == (1, 5, 2.0)
    assert benchmark.number == 1
    assert benchmark.warmup_time == 0
    assert benchmark.min_run_count == 1


@pytest.mark.parametrize(
    "suite", [complements.SmallStorageSuite, complements.LargeStorageSuite]
)
def test_storage_tracks_share_prepared_values(
    suite: type[complements.SmallStorageSuite] | type[complements.LargeStorageSuite],
) -> None:
    for name in suite.params:
        case = support.CASES[name]
        seeds = {
            case.fixture.name: support.Seed(
                "unused",
                "reference",
                case.fixture.partitions,
                {case.operation: (120, 160)},
            )
        }
        assert suite().track_total_bytes(seeds, name) == 160
        assert suite().track_growth_bytes(seeds, name) == 40


@pytest.mark.parametrize("group", ["small", "large", "combined"])
def test_storage_preparation_runs_each_state_once(
    group: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = support.SMALL[1]
    monkeypatch.chdir(tmp_path)

    def seed(fixture: support.Fixture, directory: Path, *, mixed: bool) -> support.Seed:
        state = "mixed" if mixed else "full"
        path = directory / f"{fixture.name}-{state}"
        path.write_bytes(b"seed")
        count = fixture.partitions * 9 // 10 if mixed else fixture.partitions
        return support.Seed(str(path), "reference", count)

    prepared = Mock(side_effect=seed)
    monkeypatch.setattr(support, "prepare_seed", prepared)
    monkeypatch.setattr(support.LocalRunner, "from_definitions", Mock())
    operations = []

    def execute(sample: support.Sample) -> None:
        operations.append((sample.case.fixture.name, sample.case.operation))
        sample.path.write_bytes(b"published")

    monkeypatch.setattr(support.Sample, "execute", execute)
    fixtures, mixed, states = {
        "small": (support.SMALL, support.SMALL[1:], ("cached", "mixed")),
        "large": (support.LARGE, support.LARGE[:1], ("cached",)),
        "combined": (support.COMBINED, support.COMBINED, ()),
    }[group]
    result = getattr(support, f"{group}_seeds")()
    assert Counter(
        (call.args[0], call.kwargs["mixed"]) for call in prepared.call_args_list
    ) == Counter([(f, False) for f in fixtures] + [(f, True) for f in mixed])
    assert Counter(operations) == Counter(
        (fixture.name, state) for fixture in fixtures[1:] for state in states
    )
    for fixture in fixtures[1:]:
        if states:
            assert result[fixture.name].sizes == {
                "cold": (0, 4),
                **dict.fromkeys(states, (4, 9)),
            }
    assert all(Path(seed.path).read_bytes() == b"seed" for seed in result.values())


def test_identity_benchmark_uses_reusable_graph() -> None:
    suite = preparation.IdentitySuite()
    graphs = suite.setup_cache()
    restored = pickle.loads(pickle.dumps(graphs))  # ruff: ignore[suspicious-pickle-usage] - In-memory data from this test only.
    for nodes in suite.params:
        live = preparation.compile_live_catalog(
            {"benchmark-fixture": preparation.workload(1, "catalog", nodes).plugin}
        )
        suite.setup(restored, nodes)
        assert preparation.compile_computation(suite.graph, {}) == live.computation
        suite.time_compile_identity(restored, nodes)
