"""Run isolated retained-input measurements without changing ASV workloads."""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import importlib.metadata
import json
import logging
import os
import platform
import pstats
import resource
import shutil
import signal
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy import event

from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputSelection, LocationInputs
from lyra.runtime.result_store import ResultStore
from lyra.server.response_budget import http_json_response, http_response_budget
from lyra.server.result_rows import RowPageBuilder
from test_support.retained_input_workloads import retained_inputs, retained_workload

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sized

    import geopandas as gpd

    from lyra.runtime.location_inputs import InputBindings


@dataclass(frozen=True)
class Case:
    """One fixed fixture and observable operation."""

    graph: str
    partitions: int
    references: int
    operation: str

    @property
    def name(self) -> str:
        """The stable CLI selector and artifact stem."""
        return f"{self.graph}-{self.partitions}-{self.references}-{self.operation}"

    @property
    def largest(self) -> bool:
        """Whether this fixture receives diagnostic passes."""
        return self.partitions == 1000 and self.references in {0, 10000}


def cases() -> list[Case]:
    """Return the planned matrix without altering ASV definitions."""
    result = []
    fixtures = [("direct", n, r) for n in (10, 100, 1000) for r in (100, 10000)]
    fixtures += [(g, n, 0) for g in ("full", "per_location") for n in (100, 1000)]
    for graph, partitions, references in fixtures:
        operations = ["cached", "description", "candidates"]
        if (graph == "direct" and partitions in {10, 1000} and references == 10000) or (
            graph != "direct" and partitions == 1000
        ):
            operations += ["cold", "mixed"]
        if partitions == 1000 and references in {0, 10000}:
            operations += ["native", "stream", "page"]
        result.extend(Case(graph, partitions, references, op) for op in operations)
    return result


def modes(case: Case, requested: str) -> list[str]:
    """Return eligible, independent measurement passes."""
    eligible = ["baseline"]
    if case.largest:
        if case.operation in {"cached", "description", "page", "cold", "mixed"}:
            eligible += ["cpu", "sql"]
        if case.graph != "full" and case.operation in {
            "cached",
            "description",
            "native",
        }:
            eligible += ["memory"]
    return eligible if requested == "all" else [m for m in eligible if m == requested]


def check(condition: object, message: str) -> None:
    """Require a workload invariant outside measurement.

    Raises:
        ValueError: If the fixture or operation violates its contract.
    """
    if not condition:
        raise ValueError(message)


def warm_partitions(case: Case) -> int:
    """Return the number of partitions in the immutable seed."""
    if case.operation == "cold":
        return 0
    return case.partitions * 9 // 10 if case.operation == "mixed" else case.partitions


def prepare(case: Case, path: Path) -> str:
    """Return the reference from a seed closed before measurement."""
    count = warm_partitions(case)
    if not count:
        return ""
    work = retained_workload(
        derived=case.graph != "direct",
        policy="full" if case.graph == "full" else "per_location",
    )
    runner = LocalRunner.from_definitions(
        {"bench": work.plugin}, result_store_path=path
    )
    return runner.run(
        "root", inputs=retained_inputs(count, case.references)
    ).metadata.job_id


def operation(case: Case, path: Path, reference: str) -> tuple[Callable, list]:
    """Return an operation closure and handler log prepared outside timing."""
    work = retained_workload(
        derived=case.graph != "direct",
        policy="full" if case.graph == "full" else "per_location",
    )
    runner = LocalRunner.from_definitions(
        {"bench": work.plugin}, result_store_path=path
    )
    inputs = retained_inputs(case.partitions, case.references)
    name = "reference" if case.graph == "direct" else "targets"

    def execute() -> object:
        if case.operation in {"cold", "mixed", "cached"}:
            return runner.run("root", inputs=inputs)
        with ResultStore.open(path) as store:
            if case.operation == "description":
                return store.describe_result(reference)
            if case.operation == "native":
                return store.read_location_input(reference, name)
            if case.operation == "stream":
                return sum(
                    len(chunk) for chunk in store.stream_location_input(reference, name)
                )
            if case.operation == "page":
                page = RowPageBuilder(
                    store, "http://profiling.invalid", http_response_budget()
                ).build_location_input(reference, name, limit=100)
                return http_json_response(page).body
            with store.location_input_page_candidates(
                reference, name, limit=100
            ) as page:
                return list(page.rows)

    return execute, work.calls


@contextmanager
def sql_probe() -> Generator[dict]:
    """Yield cursor timing and load counts; restore hooks even on failure."""
    shapes: dict[str, dict] = {}
    loads: Counter[str] = Counter()
    bindings: Counter[str] = Counter()
    data = {"shapes": shapes}
    decoded = {"selection_json_bytes": 0}
    original_decode = InputSelection.model_validate_json

    def decode(value: str) -> InputSelection:
        decoded["selection_json_bytes"] += len(value.encode())
        return original_decode(value)

    original_load = LocationInputs.load
    original_bindings = LocationInputs.bindings

    def load(
        self: LocationInputs, connection: sa.Connection, key: str
    ) -> InputSelection:
        loads[key] += 1
        return original_load(self, connection, key)

    def binding(
        self: LocationInputs, connection: sa.Connection, key: str
    ) -> InputBindings:
        bindings[key] += 1
        return original_bindings(self, connection, key)

    starts: dict[int, float] = {}

    def before(
        _conn: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        context: object,
        _many: object,
    ) -> None:
        starts[id(context)] = time.perf_counter()

    def after(
        _conn: object,
        _cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: object,
    ) -> None:
        elapsed = time.perf_counter() - starts.pop(id(context))
        entry = shapes.setdefault(
            statement,
            {"count": 0, "cursor_seconds": 0.0, "parameters": parameters, "many": many},
        )
        entry["count"] += 1
        entry["cursor_seconds"] += elapsed

    event.listen(sa.engine.Engine, "before_cursor_execute", before)
    event.listen(sa.engine.Engine, "after_cursor_execute", after)
    try:
        with (
            patch.object(InputSelection, "model_validate_json", decode),
            patch.object(LocationInputs, "load", load),
            patch.object(LocationInputs, "bindings", binding),
        ):
            yield data
    finally:
        event.remove(sa.engine.Engine, "before_cursor_execute", before)
        event.remove(sa.engine.Engine, "after_cursor_execute", after)
        data.update(decoded)
        data.update(
            selection_loads=sum(loads.values()),
            distinct_selection_loads=len(loads),
            binding_visits=sum(bindings.values()),
            distinct_bindings=len(bindings),
        )


def measure(execute: Callable, mode: str, stem: Path) -> tuple[object, dict]:
    """Return the operation value and measurements; write artifacts after timing."""
    profile = cProfile.Profile()
    details = {}
    if mode == "memory":
        tracemalloc.start()
    try:
        if mode == "cpu":
            profile.enable()
        wall, cpu = time.perf_counter(), time.process_time()
        value = execute()
        elapsed = {
            "wall_seconds": time.perf_counter() - wall,
            "cpu_seconds": time.process_time() - cpu,
        }
    finally:
        profile.disable()
        if mode == "memory":
            details["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
            details["allocation_sites"] = [
                str(s) for s in tracemalloc.take_snapshot().statistics("lineno")[:30]
            ]
            tracemalloc.stop()
    if mode == "cpu":
        profile.dump_stats(str(stem.with_suffix(".prof")))
        with stem.with_suffix(".pstats.txt").open("w") as stream:
            stats = pstats.Stats(profile, stream=stream)
            stats.sort_stats("tottime").print_stats(60)
            stats.sort_stats("cumulative").print_stats(60).print_callers(40)
    return value, {
        **elapsed,
        **details,
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def validate(case: Case, value: Sized | int | bytes, calls: list) -> None:
    """Check returned behavior and cache acquisition after timing."""
    if case.operation == "cached":
        check(not calls, "Cached execution invoked a handler or acquisition")
    if case.operation == "mixed":
        root_ids = set().union(*(ids for name, ids in calls if name == "root"))
        expected = {f"s{i}" for i in range(case.partitions * 9 // 10, case.partitions)}
        if case.graph == "full":
            expected = {f"s{i}" for i in range(case.partitions)}
        check(root_ids == expected, "Mixed metric partitions differ")
        if case.graph != "direct":
            related = set().union(*(ids for name, ids in calls if name == "related"))
            expected = (
                {f"s{i}" for i in range(case.partitions)}
                if case.graph == "full"
                else expected
            )
            check(related == expected, "Operation invalidation differs")
    if case.operation == "stream":
        check(cast("int", value) > 0, "Arrow stream contained no encoded bytes")
    if case.operation == "native":
        frame = cast("gpd.GeoDataFrame", value)
        expected_rows = (
            case.references if case.graph == "direct" else case.partitions * 100
        )
        check(len(frame) == expected_rows, "Incomplete retained rows")
        if case.graph == "direct":
            check(
                set(frame.location_id) == {f"r{i}" for i in range(case.references)},
                "Reference identities differ",
            )
        else:
            check(
                set(zip(frame.source_location_id, frame.location_id, strict=True))
                == {
                    (f"s{i}", f"t{j}")
                    for i in range(case.partitions)
                    for j in range(100)
                },
                "Dense correspondence pairs differ",
            )
    if case.operation == "page":
        check(
            len(json.loads(cast("bytes", value))["rows"]) == 100,
            "Incomplete encoded page",
        )
    if case.operation == "candidates":
        check(len(cast("Sized", value)) == 100, "Incomplete SQL candidates")


def explain(path: Path, details: dict) -> None:
    """Inspect SELECT plans outside instrumentation and measurement."""
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            for statement, entry in details["shapes"].items():
                parameters = entry.pop("parameters")
                if (
                    statement.lstrip().upper().startswith("SELECT")
                    and not entry["many"]
                ):
                    entry["plan"] = [
                        tuple(row)
                        for row in connection.exec_driver_sql(
                            "EXPLAIN QUERY PLAN " + statement, parameters
                        )
                    ]
    finally:
        engine.dispose()


def validate_store(case: Case, path: Path) -> None:
    """Check reference membership sharing after execution outside measurement."""
    if case.operation not in {"cold", "mixed", "cached"} or not case.references:
        return
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            selections = [
                InputSelection.model_validate_json(row[0])
                for row in connection.execute(
                    sa.text("SELECT description FROM input_selections")
                )
            ]
        shared = [selection for selection in selections if "r0" in selection.records]
        check(len(shared) == 1, "Reference membership must be stored once")
        check(
            set(shared[0].records) == {f"r{i}" for i in range(case.references)},
            "Shared reference membership is incomplete",
        )
    finally:
        engine.dispose()


def worker(
    case: Case, mode: str, output: Path, seed_directory: Path | None = None
) -> None:
    """Prepare an immutable seed and disposable sample copies in a fresh process."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="lyra-profile-") as directory:
        seed = (seed_directory or Path(directory)) / (
            f"{case.graph}-{case.partitions}-{case.references}-"
            f"{warm_partitions(case)}.sqlite"
        )
        metadata = seed.with_suffix(".json")
        reused = metadata.exists()
        if reused:
            seed_info = json.loads(metadata.read_text())
        else:
            reference = prepare(case, seed)
            seed_info = {
                "reference": reference,
                "seconds": time.perf_counter() - started,
            }
            metadata.write_text(json.dumps(seed_info))
        reference = str(seed_info["reference"])
        setup_seconds = seed_info["seconds"]
        samples = []
        for index in range(3 if mode == "baseline" else 1):
            path = Path(directory) / f"sample-{index}.sqlite"
            if seed.exists():
                shutil.copy2(seed, path)
            execute, calls = operation(case, path, reference)
            stem = output / f"{case.name}-{mode}-{index}"
            if mode == "sql":
                with sql_probe() as details:
                    value, sample = measure(execute, mode, stem)
                explain(path, details)
                sample["sql"] = details
            else:
                value, sample = measure(execute, mode, stem)
            validate(case, cast("Sized | int | bytes", value), calls)
            validate_store(case, path)
            samples.append(sample)
            path.unlink(missing_ok=True)
        result = {
            "case": asdict(case),
            "mode": mode,
            "setup_seconds": setup_seconds,
            "seed_reused": reused,
            "samples": samples,
        }
        result["summary"] = {
            key: {
                "median": statistics.median(s[key] for s in samples),
                "min": min(s[key] for s in samples),
                "max": max(s[key] for s in samples),
            }
            for key in ("wall_seconds", "cpu_seconds")
        }
        (output / f"{case.name}-{mode}.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )


def hardware() -> dict[str, str]:
    """Return available Linux CPU and physical memory descriptions."""
    return {
        name: path.read_text(encoding="utf-8") if path.exists() else "unavailable"
        for name, path in {
            "cpu": Path("/proc/cpuinfo"),
            "memory": Path("/proc/meminfo"),
        }.items()
    }


async def main() -> None:
    """Run selected cases sequentially and preserve provenance and child logs."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(".asv/retained-input-profiling")
    )
    parser.add_argument(
        "--case", action="append", help="Exact case name; repeat to select several"
    )
    parser.add_argument(
        "--mode", choices=["baseline", "cpu", "sql", "memory", "all"], default="all"
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--seed-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    selected = [c for c in cases() if not args.case or c.name in args.case]
    if args.case and set(args.case) - {c.name for c in selected}:
        parser.error("Unknown case; use --list")
    if args.list:
        for case in selected:
            sys.stdout.write(case.name + "\n")
        return
    if args.worker:
        worker(selected[0], args.mode, args.output, args.seed_directory)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(
        tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=args.output)
    )
    provenance = {
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
        "hardware": hardware(),
        "load_average": os.getloadavg(),
        "threads": {
            key: value
            for key, value in os.environ.items()
            if key.endswith("NUM_THREADS") or key == "POLARS_MAX_THREADS"
        },
        "packages": {
            d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
        },
        "argv": sys.argv,
    }
    for name, command in {
        "revision": ["git", "rev-parse", "HEAD"],
        "status": ["git", "status", "--short"],
        "diff": ["git", "diff", "HEAD"],
    }.items():
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        check(process.returncode == 0, "Git provenance failed")
        provenance[name] = stdout.decode()
    (output / "environment.json").write_text(json.dumps(provenance, indent=2) + "\n")
    shutil.copy2(__file__, output / "harness.py")
    shutil.copy2("test_support/retained_input_workloads.py", output / "fixture.py")
    logging.getLogger(__name__).info("%s", output)
    with tempfile.TemporaryDirectory(prefix="lyra-profile-seeds-") as seed_directory:
        for case in selected:
            for mode in (
                [args.mode]
                if args.case and args.mode != "all"
                else modes(case, args.mode)
            ):
                await run_child(case, mode, output, seed_directory)


async def run_child(case: Case, mode: str, output: Path, seed_directory: str) -> None:
    """Run one child and wait for its cleanup if interrupted.

    Raises:
        asyncio.CancelledError: After the interrupted child finishes cleanup.
    """
    logging.getLogger(__name__).info("%s %s", case.name, mode)
    with (output / f"{case.name}-{mode}.log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "build_scripts.profile_retained_inputs",
            "--worker",
            "--case",
            case.name,
            "--mode",
            mode,
            "--output",
            str(output),
            "--seed-directory",
            seed_directory,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            status = await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
                await process.wait()
            raise
        check(status == 0, f"Worker failed; see {log.name}")


if __name__ == "__main__":
    asyncio.run(main())
