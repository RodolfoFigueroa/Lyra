"""Native execution and filesystem cache costs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from lyra.runtime.cache import TableCache
from lyra.runtime.local import LocalRunner
from test_support.canonical_execution import canonical_workload
from test_support.workloads import workload


class ExecutionSuite:
    """Execute reusable graphs through the public local interface."""

    version = "1"
    params: ClassVar[list[str]] = [
        "chain-small",
        "chain-large",
        "diamond-small",
        "diamond-large",
    ]
    param_names: ClassVar[list[str]] = ["case"]

    def setup(self, case: str) -> None:
        """Compile once before repeated local executions."""
        shape, size = case.split("-")
        rows = 1 if size == "small" else 1000
        self.work = workload(rows, shape)
        self.runner = LocalRunner.from_definitions(
            {"benchmark-fixture": self.work.plugin}
        )

    def peakmem_local(self, case: str) -> None:
        """Measure execution memory for chains and shared dependencies."""
        self.time_local(case)

    def time_local(self, _case: str) -> None:
        """Include invocation preparation, resource scope, and validation."""
        self.runner.run("root", {})


class CacheSuite:
    """Measure genuinely empty, populated, and revision-invalidated caches."""

    version = "1"
    params: ClassVar[list[str]] = ["empty", "warm", "revision"]
    param_names: ClassVar[list[str]] = ["state"]
    number = 1
    warmup_time = 0

    def setup(self, state: str) -> None:
        """Reset storage for each individual measurement."""
        self.directory = TemporaryDirectory(prefix="lyra-benchmark-")
        self.work = workload(1000, "diamond")
        self.runner = LocalRunner.from_definitions(
            {"benchmark-fixture": self.work.plugin},
            artifact_dir=Path(self.directory.name),
        )
        self.revision = "first"
        if state != "empty":
            self.runner.run("root", {}, cache_revision=self.revision)
        if state == "revision":
            self.revision = "second"
        self.work.calls.clear()

    def time_execute(self, _state: str) -> None:
        """Execute one invocation with the prepared cache state."""
        self.runner.run("root", {}, cache_revision=self.revision)

    def teardown(self, _state: str) -> None:
        """Remove all temporary cache artifacts."""
        self.directory.cleanup()


class CanonicalExecutionSuite:
    """Measure accepted native and routed batches without request preparation."""

    version = "2"
    params: ClassVar[list[list[object]]] = [["native", "routed"], [1, 1000], [1, 2]]
    param_names: ClassVar[list[str]] = ["routed", "rows", "inputs"]

    def setup(self, routed: str, rows: int, inputs: int) -> None:
        """Accept the batch before timing execution."""
        self.work = canonical_workload(routed, rows, inputs)

    def time_execute(self, _routed: str, _rows: int, _inputs: int) -> None:
        """Acquire and execute an already accepted route."""
        self.work.executor.execute(self.work.prepared)


class CanonicalCacheSuite:
    """Routed cold, warm, and selection-only invalidation costs."""

    version = "1"
    params: ClassVar[list[list[object]]] = [["cold", "warm", "selection"], [1, 1000]]
    param_names: ClassVar[list[str]] = ["state", "rows"]
    number = 1
    warmup_time = 0

    def setup(self, state: str, rows: int) -> None:
        """Prepare cache state outside the measured execution."""
        self.work = canonical_workload("routed", rows, 1)
        self.directory = TemporaryDirectory()
        cache = TableCache(Path(self.directory.name))
        self.work.executor.cache = cache
        if state != "cold":
            self.work.executor.execute(self.work.prepared)
        if state == "selection":
            self.work = canonical_workload("routed", rows, 1, revision="source")
            self.work.executor.cache = cache

    def time_execute(self, _state: str, _rows: int, _inputs: int = 1) -> None:
        """Resolve the route before looking up the native metric."""
        self.work.executor.execute(self.work.prepared)

    def teardown(self, _state: str, _rows: int) -> None:
        """Remove temporary artifacts."""
        self.directory.cleanup()
