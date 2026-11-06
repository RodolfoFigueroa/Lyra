"""Batch acquisition and explicit location-operation execution."""

from typing import ClassVar

from test_support.location_workloads import location_workload


class LocationExecutionSuite:
    """Resolve a public batch and execute an explicitly bound location operation."""

    version = "1"
    params: ClassVar[list[int]] = [1, 1000]

    def setup(self, rows: int) -> None:
        """Declare the provider, selector, and consuming calculation."""
        self.runner, self.inputs = location_workload(rows)

    def time_local(self, _rows: int) -> None:
        """Include acquisition, operation execution, and result validation."""
        self.runner.run("root", self.inputs)

    def peakmem_local(self, _rows: int) -> None:
        """Measure whole-process memory during batch execution."""
        self.runner.run("root", self.inputs)
