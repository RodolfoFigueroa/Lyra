"""Offline investigation CLI; all measurements use independent child processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
import traceback
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pyarrow as pa

from benchmarks.schema_storage.investigation_cases import cases, fixture
from benchmarks.schema_storage.investigation_concurrency import campaign
from benchmarks.schema_storage.investigation_runtime import (
    memory,
    profile,
    rss_sum,
    sample,
    save,
    spawn,
    wait_for,
)
from benchmarks.schema_storage.investigation_work import Work, require
from benchmarks.schema_storage.run import fidelity

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess

    from benchmarks.schema_storage.investigation_cases import Case


def worker(args: argparse.Namespace) -> None:
    """Measure a single case/batch, preserving samples before any failure."""
    case = next(case for case in cases() if case.name == args.case[0])
    result: dict = {
        "case": asdict(case),
        "stage": args.stage,
        "batch": args.batch,
        "records": [],
        "status": "running",
    }
    destination = args.output / f"{case.name}-{args.stage}-{args.batch}.json"
    save(destination, result)
    try:
        measure_worker(args, case, result, destination)
    except Exception:
        result["status"] = "failed"
        result["error"] = traceback.format_exc()
        raise
    finally:
        save(destination, result)


def measure_worker(
    args: argparse.Namespace, case: Case, result: dict, destination: Path
) -> None:
    """Collect an isolated batch and checkpoint each accepted observation."""
    with TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        work = Work(fixture(case, args.seed))
        result["prepared_memory"] = memory(os.getpid())
        operations = args.operation or work.operations()
        for name in operations:
            repeats = args.samples // args.batches if args.stage == "timing" else 1
            for repetition in range(-2 if args.stage == "timing" else 0, repeats):
                directory = scratch / "sample"
                directory.mkdir(exist_ok=True)
                operation = work.prepare(name, directory)
                if args.stage == "profile":
                    record = profile(
                        operation,
                        args.output / f"{case.name}-{name.replace(':', '-')}.prof",
                    )
                elif args.stage == "memory":
                    ready = args.output / f"{case.name}.ready"
                    ready.write_text(
                        json.dumps({"operation": name, **memory(os.getpid())})
                    )
                    go = ready.with_suffix(".go")
                    wait_for(go)
                    go.unlink()
                    measured = operation.action()
                    end_memory = memory(os.getpid())
                    ready.unlink()
                    ack = ready.with_suffix(".ack")
                    wait_for(ack)
                    ack.unlink()
                    operation.verify(measured)
                    record = {"retained_memory": end_memory, **operation.metadata}
                    del measured
                else:
                    record = sample(operation)
                if repetition >= 0:
                    result["records"].append(
                        {"operation": name, "repeat": repetition, **record}
                    )
                    save(destination, result)
                del operation
                shutil.rmtree(directory)
        result["status"] = "passed"


def environment() -> dict:
    """Return environment and source identity for campaign reproduction."""
    head = Path(".git/HEAD").read_text(encoding="utf-8").strip()
    revision = (
        Path(".git", head[5:]).read_text(encoding="utf-8").strip()
        if head.startswith("ref: ")
        else head
    )
    sources = sorted(
        [
            *Path("benchmarks").rglob("*.py"),
            *Path("packages").rglob("*.py"),
            *Path("lyra_app").rglob("*.py"),
        ]
    )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "arrow_threads": pa.cpu_count(),
        "arrow_io_threads": pa.io_thread_count(),
        "versions": {
            name: version(name)
            for name in ("pandas", "pandera", "pyarrow", "geopandas", "shapely")
        },
        "revision": revision,
        "git_index_sha256": hashlib.sha256(Path(".git/index").read_bytes()).hexdigest(),
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "working_tree_identity": "Working source hashes; no clean-tree claim.",
        "temporary_directory": str(Path(TemporaryDirectory().name).parent),
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        ),
    }


def launch(args: argparse.Namespace, case: Case, stage: str, batch: int) -> dict:
    """Return child status after the requested independent measurement pass."""
    child_args = argparse.Namespace(
        **{**vars(args), "case": [case.name], "stage": stage, "batch": batch}
    )
    path = args.output / f"{case.name}-{stage}-{batch}.log"
    process = spawn(worker, (child_args,), path)
    try:
        if stage == "memory":
            measurements = observe_memory(process, args.output / f"{case.name}.ready")
            save(args.output / f"{case.name}-memory-samples.json", measurements)
        process.join()
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
    return {
        "case": case.name,
        "stage": stage,
        "batch": batch,
        "exit_code": process.exitcode,
        "log": path.name,
    }


def observe_memory(process: BaseProcess, ready: Path) -> list[dict]:
    """Return samples from acknowledged per-operation memory barriers."""
    measurements = []
    active = None
    while process.is_alive():
        if ready.exists():
            if active is None:
                try:
                    active = json.loads(ready.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                active["sampled_peak_rss"] = active.get("VmRSS", 0)
                ready.with_suffix(".go").touch()
            active["sampled_peak_rss"] = max(
                active["sampled_peak_rss"], rss_sum([process])
            )
        elif active is not None:
            measurements.append(active)
            active = None
            ready.with_suffix(".ack").touch()
        time.sleep(0.001)
    if active is not None:
        measurements.append(active)
    return measurements


def parser() -> argparse.ArgumentParser:
    """Return the campaign CLI."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--stage",
        choices=("all", "timing", "profile", "memory", "concurrency"),
        default="all",
    )
    result.add_argument(
        "--case", action="append", choices=[case.name for case in cases()]
    )
    result.add_argument("--operation", action="append")
    result.add_argument("--samples", type=int, default=60)
    result.add_argument("--batches", type=int, choices=(1, 2, 3), default=3)
    result.add_argument("--resume", action="store_true")
    result.add_argument("--seed", type=int, default=914)
    result.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--batch", type=int, default=0, help=argparse.SUPPRESS)
    return result


def completed(directory: Path, case: str, stage: str, batch: int) -> bool:
    """Return whether a checkpoint is complete and verified."""
    path = directory / f"{case}-{stage}-{batch}.json"
    return (
        path.exists()
        and json.loads(path.read_text(encoding="utf-8"))["status"] == "passed"
    )


def resume_manifest(args: argparse.Namespace, current: dict) -> dict:
    """Return preserved campaign history after checking fixture identity."""
    path = args.output / "manifest.json"
    if not args.resume:
        require(not path.exists(), "Output already has a manifest; use --resume")
        return current
    previous = json.loads(path.read_text(encoding="utf-8"))
    require(
        previous["configuration"]["seed"] == args.seed,
        "Cannot resume with another seed",
    )
    require(
        previous["environment"]["versions"] == current["environment"]["versions"],
        "Cannot resume with changed library versions",
    )
    previous.setdefault("resumptions", []).append(
        {
            "environment": current["environment"],
            "configuration": current["configuration"],
        }
    )
    previous["status"] = "running"
    return previous


def main() -> None:
    """Run correctness gates, then independent campaign stages."""
    cli = parser()
    args = cli.parse_args()
    if args.samples < args.batches or args.samples % args.batches:
        cli.error("samples must be a positive multiple of batches")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return

    with TemporaryDirectory() as temporary:
        gates = fidelity(Path(temporary), categories=False)
        require(all(row["error"] is None for row in gates), "Supported fidelity failed")
    with TemporaryDirectory() as temporary:
        categories = fidelity(Path(temporary))
        require(
            all(row["error"] is None for row in categories if row["state"] != "empty"),
            "Populated categorical fidelity failed",
        )
    manifest = {
        "environment": environment(),
        "configuration": {**vars(args), "output": str(args.output)},
        "fidelity": gates,
        "categories": categories,
        "children": [],
    }
    manifest = resume_manifest(args, manifest)
    save(args.output / "manifest.json", manifest)
    selected = [case for case in cases() if not args.case or case.name in args.case]
    for stage in (
        ("timing", "profile", "memory", "concurrency")
        if args.stage == "all"
        else (args.stage,)
    ):
        if stage == "concurrency":
            campaign(args.output, args.samples, args.seed, args.case)
            continue
        for case in selected:
            for batch in range(args.batches if stage == "timing" else 1):
                if args.resume and completed(args.output, case.name, stage, batch):
                    continue
                child = launch(args, case, stage, batch)
                manifest["children"].append(child)
                save(args.output / "manifest.json", manifest)
                sys.stdout.write(json.dumps(child) + "\n")
                sys.stdout.flush()

    failed = any(child["exit_code"] != 0 for child in manifest["children"])
    concurrency_path = args.output / "concurrency.json"
    if concurrency_path.exists():
        failed = failed or any(
            row["status"] != "passed"
            for row in json.loads(concurrency_path.read_text(encoding="utf-8"))
        )
    manifest["status"] = "failed" if failed else "passed"
    save(args.output / "manifest.json", manifest)
    require(not failed, "Campaign contains failed cases; see preserved results")


if __name__ == "__main__":
    main()
