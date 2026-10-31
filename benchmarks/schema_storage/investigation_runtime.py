"""Measurement and process utilities for offline investigations."""

from __future__ import annotations

import cProfile
import json
import multiprocessing
import time
from contextlib import redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.process import BaseProcess

    from benchmarks.schema_storage.investigation_work import Operation


def save(path: Path, value: object) -> None:
    """Publish one complete JSON checkpoint without truncating previous results."""
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def memory(pid: int) -> dict[str, int]:
    """Return current and high-water resident bytes, including native buffers."""
    values = {}
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition(":")
        if name in {"VmRSS", "VmHWM"}:
            values[name] = int(value.split()[0]) * 1024
    return values


def sample(
    operation: Operation, *, clock: Callable[[], float] = time.perf_counter
) -> dict:
    """Return action timings after successful untimed verification."""
    result, measured = measure_action(operation, clock=clock)
    operation.verify(result)
    return {**measured, **operation.metadata}


def measure_action(
    operation: Operation, *, clock: Callable[[], float] = time.perf_counter
) -> tuple[object, dict]:
    """Return action results and timings without running verification."""
    cpu = time.process_time()
    start = clock()
    result = operation.action()
    elapsed = clock() - start
    return result, {"seconds": elapsed, "cpu_seconds": time.process_time() - cpu}


def profile(operation: Operation, target: Path) -> dict:
    """Return separately profiled call counts and inclusive/exclusive costs."""
    profiler = cProfile.Profile()
    result = profiler.runcall(operation.action)
    profiler.dump_stats(str(target))
    operation.verify(result)
    return {
        "profile": target.name,
        "functions": [
            {
                "file": entry.code.co_filename
                if not isinstance(entry.code, str)
                else "~",
                "line": entry.code.co_firstlineno
                if not isinstance(entry.code, str)
                else 0,
                "function": entry.code.co_name
                if not isinstance(entry.code, str)
                else entry.code,
                "calls": entry.callcount,
                "recursive_calls": entry.reccallcount,
                "exclusive_seconds": entry.inlinetime,
                "inclusive_seconds": entry.totaltime,
            }
            for entry in sorted(
                profiler.getstats(), key=lambda entry: entry.inlinetime, reverse=True
            )
        ],
        **operation.metadata,
    }


def wait_for(path: Path) -> None:
    """Wait for a local process barrier with a finite deadline.

    Raises:
        TimeoutError: If the coordinator does not respond within five minutes.
    """
    deadline = time.monotonic() + 300
    while not path.exists():
        if time.monotonic() > deadline:
            message = f"Barrier timed out: {path}"
            raise TimeoutError(message)
        time.sleep(0.005)


def logged(target: Callable, arguments: tuple, log: Path) -> None:
    """Run one child with its own diagnostic log."""
    with (
        log.open("w", encoding="utf-8") as stream,
        redirect_stdout(stream),
        redirect_stderr(stream),
    ):
        target(*arguments)


def spawn(target: Callable, arguments: tuple, log: Path) -> BaseProcess:
    """Return a started, independent interpreter process."""
    process = multiprocessing.get_context("spawn").Process(
        target=logged, args=(target, arguments, log)
    )
    process.start()
    return process


def rss_sum(processes: list[BaseProcess]) -> int:
    """Return the sampled resident bytes summed over live processes."""
    total = 0
    for process in processes:
        with suppress(FileNotFoundError):
            if process.pid is not None:
                total += memory(process.pid).get("VmRSS", 0)
    return total
