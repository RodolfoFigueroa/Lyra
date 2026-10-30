"""Run each representation/workload/repetition in a fresh isolated process."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import os
import shutil
import statistics
import sys
from dataclasses import asdict, dataclass, replace
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING, Literal

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import shapely
from lyra.sdk.cache import TableCache
from pandera.pandas import Column, DataFrameSchema

from benchmarks.metric_representation.execution import (
    assert_equal,
    make_executor,
    peak_mib,
    rss_mib,
)
from benchmarks.metric_representation.storage import MODES, Mode, ResultStore
from benchmarks.metric_representation.workloads import (
    SHAPES,
    Shape,
    build_workload,
    calculation_columns,
    read_frame,
    synthetic,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

CacheMode = Literal["off", "miss", "hit"]


@dataclass(frozen=True)
class Case:
    """All inputs required to reproduce one measured child process."""

    fixture: str
    path: str
    shape: Shape
    mode: Mode
    cache: CacheMode
    directory: str
    reference: str


def check_reference(result: pd.DataFrame, reference: Path) -> None:
    """Compare against a native reference outside the measured interval.

    Raises:
        RuntimeError: If the reference cannot be restored.
    """
    if reference.exists():
        expected = TableCache(reference.parent).read(reference.name)
        if expected is None:
            msg = "Reference output is unreadable"
            raise RuntimeError(msg)
        assert_equal(result, expected["root"])
    else:
        reference.parent.mkdir(parents=True, exist_ok=True)
        TableCache(reference.parent).write(reference.name, {"root": result})


def worker(case: Case) -> dict:
    """Measure SDK execution, excluding imports, source reads, and parity checks.

    Returns:
        Timing, memory, conversion counts, and exact parity status.

    Raises:
        RuntimeError: If execution does not follow the requested cache policy.
    """
    # Warm-root hits do not need source tables; their schema is recovered from an
    # empty fixture. For execution cases, source loads are common untimed setup.
    path = Path(case.path)
    if case.cache == "hit":
        path = path.parent.parent / f"{path.parent.name}-empty" / "0.parquet"
    inputs = [
        calculation_columns(read_frame(path))
        for _ in range(3 if case.shape == "join" else 1)
    ]
    workload = build_workload(inputs, case.shape)
    store = ResultStore(case.mode)
    executor = make_executor(workload, store)
    if case.cache != "off":
        executor.cache = TableCache(Path(case.directory))
    # Initialize Pandera's backend on a tiny, unrelated table outside timing.
    DataFrameSchema({"value": Column(float)}).validate(pd.DataFrame({"value": [0.0]}))
    gc.collect()
    before = rss_mib()
    setup_peak = peak_mib()
    start = perf_counter()
    result = executor.run(workload)["root"]
    elapsed = perf_counter() - start
    peak = peak_mib()
    after = rss_mib()
    expected_calls = (
        0 if case.cache == "hit" else {"chain": 7, "fanout": 6, "join": 4}[case.shape]
    )
    if len(workload.calls) != expected_calls:
        msg = f"Unexpected handler count for cache mode {case.cache}"
        raise RuntimeError(msg)
    check_reference(result, Path(case.reference))
    return {
        **asdict(case),
        "seconds": elapsed,
        "start_rss_mib": before,
        "setup_peak_mib": setup_peak,
        "peak_rss_mib": peak,
        "retained_rss_mib": executor.retained_rss_mib,
        "end_rss_mib": after,
        "stored_tables": store.stored_tables,
        "exported_tables": store.exported_tables,
        "freeze_seconds": store.freeze_seconds,
        "export_seconds": store.export_seconds,
        "handlers": len(workload.calls),
        "result_shape": list(result.shape),
        "cache_bytes": sum(
            p.stat().st_size for p in Path(case.directory).rglob("*.parquet")
        )
        if case.cache != "off"
        else 0,
        "parity": "passed",
    }


def child_target(case: Case, output: Path) -> None:
    """Measure one case in a spawned interpreter and return JSON through a file."""
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    output.write_text(json.dumps(worker(case)), encoding="utf-8")


def run_child(case: Case) -> dict:
    """Launch a fresh interpreter with fixed native-library thread counts.

    Returns:
        The child process's measurements.

    Raises:
        RuntimeError: If a child fails or exceeds its time limit.
    """
    with TemporaryDirectory(prefix="lyra-metric-sample-") as scratch:
        output = Path(scratch) / "result.json"
        process = multiprocessing.get_context("spawn").Process(
            target=child_target, args=(case, output)
        )
        process.start()
        process.join(timeout=180)
        if process.is_alive():
            process.terminate()
            process.join()
            msg = f"Benchmark exceeded 180 seconds: {case}"
            raise RuntimeError(msg)
        if process.exitcode != 0:
            msg = f"Benchmark child failed: {case}"
            raise RuntimeError(msg)
        return json.loads(output.read_text(encoding="utf-8"))


def fixtures(
    directory: Path, rows: int, saved: Path | None, kinds: Sequence[str]
) -> dict[str, Path]:
    """Create deterministic local fixtures and locate saved metric examples.

    Returns:
        Fixture names mapped to their temporary Parquet files.

    Raises:
        FileNotFoundError: If a requested saved output is missing.
    """
    paths = {}
    for kind in kinds:
        name = f"{kind}_{rows}"
        # Keep the Python string dtype, categories, and index through TableCache's
        # metadata restoration when loading synthetic data in child processes.
        frame = synthetic(kind, rows)
        TableCache(directory).write(name, {"fixture": frame})
        paths[name] = directory / name / "0.parquet"
    if saved is not None:
        for name in ("lot_statistics", "housing_lots"):
            for manifest in sorted(saved.glob("*/manifest.json")):
                metadata = json.loads(manifest.read_text())
                if name in metadata:
                    source = manifest.parent / (f"{list(metadata).index(name)}.parquet")
                    fixture_name = f"saved_{name}"
                    TableCache(directory).write(
                        fixture_name, {"fixture": read_frame(source)}
                    )
                    paths[fixture_name] = directory / fixture_name / "0.parquet"
                    break
            else:
                msg = f"Saved metric output not found: {name}"
                raise FileNotFoundError(msg)
    for name, path in paths.items():
        TableCache(directory).write(
            f"{name}-empty", {"fixture": read_frame(path).iloc[:0]}
        )
    return paths


def summarize(records: list[dict]) -> list[dict]:
    """Aggregate medians and observed ranges without discarding slow samples.

    Returns:
        One summary for each fixture, graph, cache, and representation.
    """
    groups: dict[tuple[str, ...], list[dict]] = {}
    for record in records:
        key = tuple(record[k] for k in ("fixture", "shape", "cache", "mode"))
        groups.setdefault(key, []).append(record)
    summaries = []
    for key, samples in sorted(groups.items()):
        summary: dict = dict(
            zip(("fixture", "shape", "cache", "mode"), key, strict=True)
        )
        for name in (
            "seconds",
            "start_rss_mib",
            "setup_peak_mib",
            "peak_rss_mib",
            "retained_rss_mib",
            "end_rss_mib",
            "freeze_seconds",
            "export_seconds",
        ):
            values = [sample[name] for sample in samples]
            summary[name] = statistics.median(values)
            summary[f"{name}_range"] = [min(values), max(values)]
        for name in ("stored_tables", "exported_tables", "handlers", "cache_bytes"):
            summary[name] = samples[0][name]
        summaries.append(summary)
    return summaries


def run_matrix(args: argparse.Namespace, directory: Path) -> dict:
    """Run the paired matrix with a rotating, balanced representation order.

    Returns:
        Environment details, raw samples, and aggregated measurements.
    """
    paths = fixtures(directory, args.rows, args.saved, args.kinds)
    records = []
    for (name, path), shape in product(paths.items(), args.shapes):
        reference = directory / "references" / f"{name}-{shape}"
        # Establish independent native parity references before interleaving.
        base = Case(name, str(path), shape, "native", "off", "", str(reference))
        run_child(base)
        for cache_mode, repeat in product(args.caches, range(args.repeats)):
            offset = repeat % len(MODES)
            order = MODES[offset:] + MODES[:offset]
            for mode in order:
                cache_dir = directory / (f"{name}-{shape}-{mode}-{cache_mode}-{repeat}")
                case = Case(
                    name,
                    str(path),
                    shape,
                    mode,
                    cache_mode,
                    str(cache_dir),
                    str(reference),
                )
                if cache_mode == "hit":
                    run_child(replace(case, cache="miss", mode="native"))
                record = run_child(case)
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                record["repeat"] = repeat
                for field in ("path", "directory", "reference"):
                    del record[field]
                records.append(record)
                with args.output.with_suffix(".jsonl").open(
                    "a", encoding="utf-8"
                ) as log:
                    log.write(json.dumps(record) + "\n")
                sys.stdout.write(
                    f"{name} {shape} {cache_mode} {mode}: "
                    f"{record['seconds']:.3f}s, "
                    f"{record['peak_rss_mib']:.1f} MiB\n"
                )
                sys.stdout.flush()
    return {
        "environment": {
            "python": sys.version,
            "pandas": pd.__version__,
            "pyarrow": pa.__version__,
            "geopandas": gpd.__version__,
            "shapely": shapely.__version__,
        },
        "rows": args.rows,
        "repeats": args.repeats,
        "samples": records,
        "summary": summarize(records),
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Run the offline matrix or one child-process measurement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=("numeric", "mixed", "spatial"),
        default=["numeric", "mixed", "spatial"],
    )
    parser.add_argument("--shapes", nargs="+", choices=SHAPES, default=list(SHAPES))
    parser.add_argument(
        "--caches", nargs="+", choices=("off", "miss", "hit"), default=["off"]
    )
    parser.add_argument("--saved", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/metric-benchmark.json")
    )
    args = parser.parse_args(argv)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ARROW_NUM_THREADS",
    ):
        os.environ[name] = "1"
    os.environ["PYTHONHASHSEED"] = "0"
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".jsonl").write_text("", encoding="utf-8")
    with TemporaryDirectory(prefix="lyra-metric-benchmark-") as temporary:
        result = run_matrix(args, Path(temporary))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
