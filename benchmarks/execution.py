"""Native execution and persistent cache costs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from lyra.runtime.local import LocalRunner
from lyra.runtime.sqlite_store import SQLiteStore
from test_support.canonical_execution import canonical_workload
from test_support.receiving_workloads import receiving_workload
from test_support.workloads import workload


class ExecutionSuite:
    """Execute reusable graphs through the public local interface."""

    version = "2"
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
        self.directory = TemporaryDirectory(prefix="lyra-benchmark-")
        self.runner = LocalRunner.from_definitions(
            {"benchmark-fixture": self.work.plugin},
            result_store_path=Path(self.directory.name) / "results.sqlite3",
        )

    def teardown(self, _case: str) -> None:
        """Remove this measurement's persistent store."""
        self.directory.cleanup()

    def peakmem_local(self, case: str) -> None:
        """Measure execution memory for chains and shared dependencies."""
        self.time_local(case)

    def time_local(self, _case: str) -> None:
        """Include invocation preparation, resource scope, and validation."""
        self.runner.run("root", inputs={})


class CacheSuite:
    """Measure genuinely empty, populated, and revision-invalidated caches."""

    version = "4"
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
            result_store_path=Path(self.directory.name) / "results.sqlite3",
        )
        if state != "empty":
            self.runner.run("root", inputs={})
        if state == "revision":
            self.work = workload(1000, "diamond", code_revision="2")
            self.runner = LocalRunner.from_definitions(
                {"benchmark-fixture": self.work.plugin},
                result_store_path=Path(self.directory.name) / "results.sqlite3",
            )
        self.work.calls.clear()

    def time_execute(self, _state: str) -> None:
        """Execute one invocation with the prepared cache state."""
        self.runner.run("root", inputs={})

    def peakmem_execute(self, state: str) -> None:
        """Measure process memory for the same persisted execution."""
        self.time_execute(state)

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

    def peakmem_execute(self, routed: str, rows: int, inputs: int) -> None:
        """Measure process peak including the accepted fixture and native records."""
        self.time_execute(routed, rows, inputs)


class ReceivingExecutionSuite:
    """Measure prepared receiving routes without persistent caching."""

    version = "1"
    params: ClassVar[list[list[object]]] = [["direct", "nested"], [1, 1000]]
    param_names: ClassVar[list[str]] = ["binding", "rows"]

    def setup(self, binding: str, rows: int) -> None:
        """Accept the source selection and dependency route outside timing."""
        self.work = receiving_workload(rows, nested=binding == "nested")

    def time_execute(self, _binding: str, _rows: int) -> None:
        """Acquire records, execute the dependency route, and summarize targets."""
        self.work.executor.execute(self.work.prepared)

    def peakmem_execute(self, binding: str, rows: int) -> None:
        """Include imports, accepted bindings, acquired tables, and outputs."""
        self.time_execute(binding, rows)


class CanonicalCacheSuite:
    """Routed cold, warm, and selection-only invalidation costs."""

    version = "2"
    params: ClassVar[list[list[object]]] = [["cold", "warm", "selection"], [1, 1000]]
    param_names: ClassVar[list[str]] = ["state", "rows"]
    number = 1
    warmup_time = 0

    def setup(self, state: str, rows: int) -> None:
        """Prepare cache state outside the measured execution."""
        self.work = canonical_workload("routed", rows, 1)
        self.directory = TemporaryDirectory()
        cache = SQLiteStore(Path(self.directory.name) / "values.sqlite")
        self.work.executor.store = cache
        if state != "cold":
            self.work.executor.execute(self.work.prepared)
        if state == "selection":
            self.work = canonical_workload("routed", rows, 1, revision="source")
            self.work.executor.store = cache

    def time_execute(self, _state: str, _rows: int, _inputs: int = 1) -> None:
        """Look up the bound metric and execute missing routed work."""
        self.work.executor.execute(self.work.prepared)

    def peakmem_execute(self, state: str, rows: int) -> None:
        """Measure process memory across the same routed cache execution."""
        self.time_execute(state, rows)

    def teardown(self, _state: str, _rows: int) -> None:
        """Close the borrowed relational store and remove disposable files."""
        if self.work.executor.store is not None:
            self.work.executor.store.close()
        self.directory.cleanup()
