"""Stable retained-input cases and disposable ASV fixture lifecycles."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, ClassVar, cast

import sqlalchemy as sa

from lyra.runtime.local import LocalRunner
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.response_budget import http_json_response, http_response_budget
from lyra.server.result_rows import RowPageBuilder
from test_support.retained_input_workloads import retained_inputs, retained_workload

if TYPE_CHECKING:
    import geopandas as gpd

    from lyra.contracts.results import ResultDescription
    from lyra.runtime.results import RunResult
    from test_support.cache_workloads import CacheWorkload


@dataclass(frozen=True)
class Fixture:
    graph: str
    partitions: int
    references: int

    @property
    def name(self) -> str:
        return f"{self.graph}-p{self.partitions}-r{self.references}"

    def workload(self, revision: str = "1") -> CacheWorkload:
        return retained_workload(
            derived=self.graph != "direct",
            revision=revision,
            policy="full" if self.graph == "full" else "per_location",
        )


@dataclass(frozen=True)
class Case:
    fixture: Fixture
    operation: str

    @property
    def name(self) -> str:
        return f"{self.fixture.name}-{self.operation}"


def fixtures(partitions: int) -> tuple[Fixture, ...]:
    return (
        Fixture("direct", partitions, 100),
        Fixture("direct", partitions, 10000),
        Fixture("full", partitions, 0),
        Fixture("per_location", partitions, 0),
    )


SMALL = fixtures(100)
LARGE = fixtures(1000)
RETRIEVAL = LARGE[1:]
SMALL_CASES = tuple(Case(f, op) for f in SMALL for op in ("cached", "description"))
LARGE_CASES = tuple(
    Case(f, op) for f in LARGE for op in ("cached", "description")
) + tuple(Case(f, op) for f in RETRIEVAL for op in ("candidates", "page"))
COLD_CASES = tuple(Case(f, "cold") for f in LARGE)
MIXED_CASES = (Case(LARGE[0], "mixed"),)
SMALL_MIXED_CASES = tuple(Case(f, "mixed") for f in SMALL[1:])
MEMORY_CASES = tuple(
    Case(f, op) for f in RETRIEVAL for op in ("cached", "description", "native")
)
COMBINED = tuple(Fixture(graph, 100, 10000) for graph in ("full", "per_location"))
READ_CASES = tuple(Case(f, op) for f in RETRIEVAL for op in ("native", "stream"))
INVALIDATION_CASES = tuple(
    Case(f, op) for f in SMALL[1:] for op in ("revision", "selection")
)
COMBINED_CASES = tuple(Case(f, op) for f in COMBINED for op in ("cached", "mixed"))
COLD_MEMORY_CASES = tuple(Case(f, "cold") for f in SMALL[1:])
EXTRA_MEMORY_CASES = tuple(Case(f, "stream") for f in RETRIEVAL)
SMALL_STORAGE_CASES = tuple(
    Case(f, op) for f in SMALL[1:] for op in ("cold", "cached", "mixed")
)
LARGE_STORAGE_CASES = tuple(Case(f, op) for f in RETRIEVAL for op in ("cold", "cached"))
CASES = {
    c.name: c
    for group in (
        SMALL_CASES,
        LARGE_CASES,
        COLD_CASES,
        MIXED_CASES,
        SMALL_MIXED_CASES,
        MEMORY_CASES,
        READ_CASES,
        INVALIDATION_CASES,
        COMBINED_CASES,
        COLD_MEMORY_CASES,
        EXTRA_MEMORY_CASES,
        SMALL_STORAGE_CASES,
        LARGE_STORAGE_CASES,
    )
    for c in group
}


@dataclass(frozen=True)
class Seed:
    path: str
    reference: str
    partitions: int
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)


def prepare_seeds(
    selected: tuple[Fixture, ...], *, mixed: tuple[Fixture, ...], storage: bool = True
) -> dict[str, Seed]:
    """Prepare shared states and both byte metrics once in ASV's cache process."""
    directory = Path("seeds")
    directory.mkdir()
    try:
        return build_seeds(selected, directory, mixed=mixed, storage=storage)
    except BaseException:
        shutil.rmtree(directory)
        raise


def build_seeds(
    selected: tuple[Fixture, ...],
    directory: Path,
    *,
    mixed: tuple[Fixture, ...],
    storage: bool,
) -> dict[str, Seed]:
    """Build each state and gather storage outcomes without modifying seeds."""
    result: dict[str, Seed] = {}
    for fixture in selected:
        result[fixture.name] = prepare_seed(fixture, directory, mixed=False)
        if fixture in mixed:
            result[f"{fixture.name}-mixed"] = prepare_seed(
                fixture, directory, mixed=True
            )
    if storage:
        for fixture in selected:
            if fixture.graph == "direct" and fixture.references == 100:
                continue
            seed = result[fixture.name]
            sizes = {"cold": (0, Path(seed.path).stat().st_size)}
            for op in ("cached", "mixed") if fixture in mixed else ("cached",):
                sample = Sample()
                try:
                    sample.begin(Case(fixture, op).name, result)
                    before = sample.path.stat().st_size
                    sample.execute()
                    sizes[op] = (before, sample.path.stat().st_size)
                finally:
                    sample.teardown()
            result[fixture.name] = replace(seed, sizes=sizes)
    return result


def prepare_seed(fixture: Fixture, directory: Path, *, mixed: bool) -> Seed:
    suffix = "-mixed" if mixed else ""
    path = directory / f"{fixture.name}{suffix}.sqlite"
    count = fixture.partitions * 9 // 10 if mixed else fixture.partitions
    work = fixture.workload()
    runner = LocalRunner.from_definitions(
        {"bench": work.plugin}, result_store_path=path
    )
    reference = runner.run(
        "root", inputs=retained_inputs(count, fixture.references)
    ).metadata.job_id
    return Seed(str(path), reference, count)


def small_seeds() -> dict[str, Seed]:
    return prepare_seeds(SMALL, mixed=SMALL[1:])


def large_seeds() -> dict[str, Seed]:
    return prepare_seeds(LARGE, mixed=LARGE[:1])


def combined_seeds() -> dict[str, Seed]:
    return prepare_seeds(COMBINED, mixed=COMBINED, storage=False)


class Sample:
    """Common adapters without measured methods or benchmark discovery side effects."""

    version = "1"
    rounds = 2
    repeat = (1, 5, 2.0)
    min_run_count = 1
    number = 1
    warmup_time = 0
    timeout = 1200
    param_names: ClassVar[list[str]] = ["case"]

    def __init__(self) -> None:
        self.directory: TemporaryDirectory[str] | None = None
        self.completed = False
        self.value: object = None

    def begin(self, name: str, seeds: dict[str, Seed] | None = None) -> None:
        self.case = CASES[name]
        self.directory = TemporaryDirectory(prefix="lyra-asv-inputs-")
        self.path = Path(self.directory.name) / "sample.sqlite"
        self.completed = False
        self.value = None
        try:
            self.open_fixture(seeds)
        except BaseException:
            self.close()
            raise

    def open_fixture(self, seeds: dict[str, Seed] | None) -> None:
        if self.case.operation != "cold":
            assert seeds is not None
            key = self.case.fixture.name
            if self.case.operation == "mixed":
                key += "-mixed"
            seed = seeds[key]
            expected = self.case.fixture.partitions
            if self.case.operation == "mixed":
                expected = expected * 9 // 10
            assert seed.partitions == expected
            shutil.copyfile(seed.path, self.path)
            self.reference = seed.reference
        self.work = self.case.fixture.workload(
            "2" if self.case.operation == "revision" else "1"
        )
        self.runner = LocalRunner.from_definitions(
            {"bench": self.work.plugin}, result_store_path=self.path
        )
        self.inputs = retained_inputs(
            self.case.fixture.partitions,
            self.case.fixture.references + (self.case.operation == "selection"),
        )

    def execute(self) -> None:
        try:
            self.value = self.operation()
            self.completed = True
        except BaseException:
            self.close()
            raise

    def operation(self) -> object:
        op = self.case.operation
        if op in {"cold", "mixed", "cached", "revision", "selection"}:
            return self.runner.run("root", inputs=self.inputs)
        name = "reference" if self.case.fixture.graph == "direct" else "targets"
        with ResultStore.open(self.path) as store:
            if op == "description":
                return store.describe_result(self.reference)
            if op == "native":
                return store.read_location_input(self.reference, name)
            if op == "stream":
                return sum(
                    len(chunk)
                    for chunk in store.stream_location_input(self.reference, name)
                )
            if op == "page":
                page = RowPageBuilder(
                    store, "http://benchmark.invalid", http_response_budget()
                ).build_location_input(self.reference, name, limit=100)
                return http_json_response(page).body
            with store.location_input_page_candidates(
                self.reference, name, limit=100
            ) as page:
                return list(page.rows)

    def validate(self) -> None:
        fixture, op = self.case.fixture, self.case.operation
        if op in {"cold", "mixed", "cached", "revision", "selection"}:
            self.validate_execution()
        elif op == "description":
            description = cast("ResultDescription", self.value)
            expected_names = {"main", "reference"}
            if fixture.graph != "direct":
                expected_names.add("targets")
            assert set(description.location_inputs) == expected_names
            assert set(description.outputs) == {"data"}
        elif op == "native":
            frame = cast("gpd.GeoDataFrame", self.value)
            if fixture.graph == "direct":
                assert len(frame) == fixture.references
                assert set(frame.location_id) == {
                    f"r{i}" for i in range(fixture.references)
                }
            else:
                assert len(frame) == fixture.partitions * 100
                assert set(
                    zip(frame.source_location_id, frame.location_id, strict=True)
                ) == {
                    (f"s{i}", f"t{j}")
                    for i in range(fixture.partitions)
                    for j in range(100)
                }
        elif op == "stream":
            assert isinstance(self.value, int)
            assert self.value > 0
        elif op == "page":
            rows = json.loads(cast("bytes", self.value))["rows"]
            assert len(rows) == 100
            required = {"location_id", "original", "geometry"}
            if fixture.graph != "direct":
                required.add("source_location_id")
            assert all(required <= row.keys() for row in rows)
        else:
            assert len(cast("list", self.value)) == 100

    def validate_execution(self) -> None:
        fixture, op = self.case.fixture, self.case.operation
        result = cast("RunResult", self.value)
        frame = result.tables["data"]
        assert set(frame.location_id) == {f"s{i}" for i in range(fixture.partitions)}
        assert set(frame.value) == {fixture.references + (op == "selection")}
        expected = set()
        if op in {"cold", "revision", "selection"} or (
            op == "mixed" and fixture.graph == "full"
        ):
            expected = {f"s{i}" for i in range(fixture.partitions)}
        elif op == "mixed":
            expected = {
                f"s{i}" for i in range(fixture.partitions * 9 // 10, fixture.partitions)
            }
        if op == "cached":
            assert not self.work.calls
        for handler in ("root",) if fixture.graph == "direct" else ("root", "related"):
            actual = set().union(
                *(ids for name, ids in self.work.calls if name == handler)
            )
            assert actual == (
                set() if op == "selection" and handler == "related" else expected
            )
        if fixture.references and op not in {"revision", "selection"}:
            self.validate_shared_references()

    def validate_shared_references(self) -> None:
        """Check the storage-sharing requirement after the measured publication."""
        with (
            SQLiteStore(self.path, read_only=True) as store,
            store.engine.connect() as connection,
        ):
            selections = [
                json.loads(row["description"])
                for row in connection.execute(
                    sa.select(store.layout.input_selections)
                ).mappings()
            ]
        shared = [selection for selection in selections if "r0" in selection["records"]]
        assert len(shared) == 1
        assert set(shared[0]["records"]) == {
            f"r{i}" for i in range(self.case.fixture.references)
        }

    def close(self) -> None:
        self.value = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def teardown(self, *_params: object) -> None:
        """Validate after ASV captures time or peak RSS, then clean up."""
        try:
            if self.completed:
                self.validate()
        finally:
            self.close()
