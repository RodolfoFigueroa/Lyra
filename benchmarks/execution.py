"""Native execution and filesystem cache costs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from lyra.sdk import LocalRunner

from benchmarks.workloads import workload


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
            {"benchmark-fixture": self.work.plugin}, cache_dir=Path(self.directory.name)
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
