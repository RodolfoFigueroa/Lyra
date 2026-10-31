"""Local SDK capacity and identical-key contention without external services."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from benchmarks.schema_storage.investigation_cases import cases, fixture
from benchmarks.schema_storage.investigation_runtime import (
    measure_action,
    memory,
    rss_sum,
    save,
    spawn,
    wait_for,
)
from benchmarks.schema_storage.investigation_work import Work, require


def worker(args: argparse.Namespace) -> None:
    """Prepare jobs, execute after admission, and verify outside action timers."""
    case = next(case for case in cases() if case.name == args.case)
    work = Work(fixture(case, args.seed))
    operations = [
        work.prepare_graph(
            "shared",
            args.mode,
            args.scratch / ("shared" if args.identical else f"job-{index}"),
        )
        for index in range(args.rank, args.samples, args.workers)
    ]
    prefix = args.scratch / str(args.rank)
    prefix.with_suffix(".ready").touch()
    wait_for(args.scratch / "go")
    start = time.perf_counter()
    measured = [measure_action(operation) for operation in operations]
    end = time.perf_counter()
    peak_memory = memory(os.getpid())
    prefix.with_suffix(".finished").touch()
    records = []
    for operation, (result, timing) in zip(operations, measured, strict=True):
        if args.identical:
            require(
                cast("dict", result)["root"].iloc[0, 0] == 2 * case.rows,
                "Incorrect contended output",
            )
        else:
            operation.verify(result)
        records.append({**timing, **operation.metadata})
    save(
        prefix.with_suffix(".json"),
        {
            "records": records,
            "started": start,
            "finished": end,
            "elapsed": end - start,
            "memory": peak_memory,
        },
    )
    prefix.with_suffix(".done").touch()


def start_workers(output: Path, config: dict, scratch: Path) -> list:
    """Return started workers with separate logs."""
    processes = []
    for rank in range(config["workers"]):
        arguments = argparse.Namespace(**config, rank=rank, scratch=scratch)
        if config["identical"]:
            arguments.samples = config["workers"]
        log = output / (
            f"{config['case']}-{config['mode']}-{config['workers']}-"
            f"{config['identical']}-{rank}.log"
        )
        processes.append(spawn(worker, (arguments,), log))
    return processes


def run_group(output: Path, config: dict) -> dict:
    """Return capacity observations after admitting prepared workers together."""
    case, mode, workers, identical = (
        config[key] for key in ("case", "mode", "workers", "identical")
    )
    with TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        processes = []
        try:
            processes.extend(start_workers(output, config, scratch))
            deadline = time.monotonic() + 300
            while not all(
                (scratch / f"{rank}.ready").exists() for rank in range(workers)
            ):
                require(
                    all(process.is_alive() for process in processes),
                    "Concurrency worker failed during preparation",
                )
                require(time.monotonic() < deadline, "Concurrency preparation timeout")
                time.sleep(0.01)
            prepared = rss_sum(processes)
            peak = prepared
            start = time.perf_counter()
            (scratch / "go").touch()
            while not all(
                (scratch / f"{rank}.finished").exists() for rank in range(workers)
            ):
                require(
                    all(process.exitcode in {None, 0} for process in processes),
                    "Concurrency worker failed",
                )
                total = rss_sum(processes)
                peak = max(peak, total)
                time.sleep(0.001)
            admitted_elapsed = time.perf_counter() - start
            for process in processes:
                process.join()
            children = [
                json.loads((scratch / f"{rank}.json").read_text(encoding="utf-8"))
                for rank in range(workers)
            ]
            for process in processes:
                process.join()
            require(
                all(process.exitcode == 0 for process in processes),
                "Concurrency worker exited unsuccessfully",
            )
            source_calls = sum(
                record["handlers"].count("source")
                for child in children
                for record in child["records"]
            )
            if identical:
                require(source_calls == 1, "Contended cold key computed more than once")
            elapsed = max(child["finished"] for child in children) - min(
                child["started"] for child in children
            )
            return {
                "case": case,
                "mode": mode,
                "workers": workers,
                "identical_key": identical,
                "wall_seconds": elapsed,
                "admission_seconds": admitted_elapsed,
                "jobs_per_second": sum(len(child["records"]) for child in children)
                / elapsed,
                "prepared_rss_sum": prepared,
                "sampled_peak_rss_sum": peak,
                "children": children,
                "status": "passed",
            }
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join()


def campaign(output: Path, samples: int, seed: int, selected: list[str] | None) -> None:
    """Persist each completed group, retaining failures without hiding them."""
    results = []
    for case in ("points-10000", "polygons-128-1000"):
        if selected and case not in selected:
            continue
        for workers in (1, 2, 4):
            if workers > len(os.sched_getaffinity(0)):
                continue
            for mode in ("cold", "full", "intermediate", "identical"):
                try:
                    result = run_group(
                        output,
                        {
                            "case": case,
                            "mode": "cold" if mode == "identical" else mode,
                            "workers": workers,
                            "samples": samples,
                            "seed": seed,
                            "identical": mode == "identical",
                        },
                    )
                except (AssertionError, OSError, TimeoutError) as error:
                    result = {
                        "case": case,
                        "mode": mode,
                        "workers": workers,
                        "status": "failed",
                        "error": str(error),
                    }
                results.append(result)
                save(output / "concurrency.json", results)
                sys.stdout.write(
                    f"Concurrency {case} {workers} {mode}: {result['status']}\n"
                )
                sys.stdout.flush()


def main() -> None:
    """Run one local worker process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--mode", required=True)
    for name in ("workers", "rank", "samples", "seed"):
        parser.add_argument(f"--{name}", type=int, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--identical", action="store_true")
    worker(parser.parse_args())


if __name__ == "__main__":
    main()
