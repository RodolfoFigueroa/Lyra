"""Batch acquisition and explicit location-operation execution."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from test_support.location_workloads import location_workload


class LocationExecutionSuite:
    """Resolve a public batch and execute an explicitly bound location operation."""

    version = "3"
    params: ClassVar[list[int]] = [1, 1000]

    def setup(self, rows: int) -> None:
        """Declare the provider, selector, and consuming calculation."""
        self.directory = TemporaryDirectory(prefix="lyra-benchmark-")
        self.runner, self.inputs = location_workload(
            rows, result_store_path=Path(self.directory.name) / "results.sqlite3"
        )

    def teardown(self, _rows: int) -> None:
        """Remove this measurement's retained results."""
        self.directory.cleanup()

    def time_local(self, _rows: int) -> None:
        """Include acquisition, operation execution, and result validation."""
        self.runner.run("root", inputs=self.inputs)

    def peakmem_local(self, _rows: int) -> None:
        """Measure whole-process memory during batch execution."""
        self.runner.run("root", inputs=self.inputs)
