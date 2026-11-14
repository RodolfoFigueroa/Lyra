"""Partial and hybrid node reuse with durable result assembly."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from lyra.runtime.local import LocalRunner
from lyra.runtime.result_store import ResultStore
from test_support.cache_workloads import cache_inputs, cache_workload


class NodeCacheSuite:
    """Execute one overlapping selection against a controlled store."""

    version = "1"
    params: ClassVar[list[list[str]]] = [
        ["direct", "hybrid"],
        ["cold", "warm", "partial", "invalidated"],
    ]
    param_names: ClassVar[list[str]] = ["graph", "state"]
    number = 1
    warmup_time = 0

    def setup(self, graph: str, state: str) -> None:
        """Prepare 100 partitions, half overlapping for partial reuse."""
        self.directory = TemporaryDirectory(prefix="lyra-node-cache-")
        self.path = Path(self.directory.name) / "results.sqlite"
        self.work = cache_workload(hybrid=graph == "hybrid")
        self.runner = LocalRunner.from_definitions(
            {"bench": self.work.plugin}, result_store_path=self.path
        )
        self.inputs = cache_inputs(
            50 if state == "partial" else 0, 100, hybrid=graph == "hybrid"
        )
        if state != "cold":
            self.runner.run(
                "root", inputs=cache_inputs(0, 100, hybrid=graph == "hybrid")
            )
        if state == "invalidated":
            self.work = cache_workload(hybrid=graph == "hybrid", revision="2")
            self.runner = LocalRunner.from_definitions(
                {"bench": self.work.plugin}, result_store_path=self.path
            )
        self.work.calls.clear()

    def time_execute(self, _graph: str, _state: str) -> None:
        """Include acceptance, lookup, execution, publication and output assembly."""
        self.runner.run("root", inputs=self.inputs)

    def peakmem_execute(self, graph: str, state: str) -> None:
        """Include imports and prepared cache state in process peak memory."""
        self.time_execute(graph, state)

    def teardown(self, _graph: str, _state: str) -> None:
        """Remove disposable storage."""
        self.directory.cleanup()


class AssemblySuite:
    """Reopen a result referencing fresh and reused partitions."""

    version = "1"
    params: ClassVar[list[str]] = ["direct", "hybrid"]
    param_names: ClassVar[list[str]] = ["graph"]

    def setup(self, graph: str) -> None:
        """Publish a half-reused result before measuring reads."""
        self.execution = NodeCacheSuite()
        self.execution.setup(graph, "partial")
        result = self.execution.runner.run("root", inputs=self.execution.inputs)
        self.reference = result.metadata.result_ref

    def time_read(self, _graph: str) -> None:
        """Open storage and reconstruct all groups from the durable reference."""
        with ResultStore.open(self.execution.path) as reader:
            reader.read_result(self.reference)

    def peakmem_read(self, graph: str) -> None:
        """Measure process memory for durable mixed-fragment reconstruction."""
        self.time_read(graph)

    def teardown(self, graph: str) -> None:
        """Remove the prepared result store."""
        self.execution.teardown(graph, "partial")
