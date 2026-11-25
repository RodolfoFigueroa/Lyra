"""Catalog construction and steady-state discovery scaling."""

import json
from typing import ClassVar

from asv_runner.benchmarks.mark import benchmark

from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.graph import build_graph
from lyra.sdk.plugin import PluginDefinition
from test_support.edition_catalog import discovery
from test_support.routing_plugins import conversion, provider, score_plugin
from test_support.workloads import catalog, installed, workload


class CatalogSuite:
    """Compile a shared-source graph and its public registry."""

    version = "3"
    params: ClassVar[list[int]] = [10, 100, 1000]
    param_names: ClassVar[list[str]] = ["nodes"]

    def setup(self, nodes: int) -> None:
        """Prepare live declarations outside the timed operation."""
        self.installed = installed(workload(1, "catalog", nodes).plugin)
        self.catalog = catalog(self.installed)
        self.definitions = {
            owner: plugin.definition
            for owner, plugin in self.catalog.compiled.implementations.items()
        }

    @benchmark(version="4")
    def time_graph(self, _nodes: int) -> None:
        """Validate graph dependencies."""
        build_graph(self.definitions)

    def time_catalog(self, _nodes: int) -> None:
        """Compile contracts and capture application routing."""
        catalog(self.installed)

    def time_discovery(self, _nodes: int) -> None:
        """Build and serialize a public catalog."""
        json.dumps(
            [
                item.model_dump(mode="json")
                for item in self.catalog.discovery.search(limit=_nodes)
            ]
        )

    def track_catalog_bytes(self, _nodes: int) -> int:
        """Return UTF-8 public discovery size."""
        return len(
            json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in self.catalog.discovery.search(limit=_nodes)
                ]
            ).encode()
        )

    def peakmem_compile(self, _nodes: int) -> None:
        """Measure whole-process peak during catalog compilation."""
        catalog(self.installed)


class RunnerSuite:
    """Measure runner initialization with a representative small plugin."""

    version = "1"

    def setup(self) -> None:
        """Prepare reusable handlers outside runner construction."""
        self.plugin = workload(1, "catalog", 10).plugin

    def time_initialize(self) -> None:
        """Compile the local runner's graph and resource declarations."""
        LocalRunner.from_definitions({"benchmark-fixture": self.plugin})


class AcceptedSourcesSuite:
    """Compile native source selections across target providers."""

    version = "2"
    params: ClassVar[list[list[int]]] = [[10, 100], [10, 100]]
    param_names: ClassVar[list[str]] = ["providers", "metrics"]

    def setup(self, providers: int, metrics: int) -> None:
        """Register sources sharing a target and metrics requiring that target."""
        names = ["native", "other", *(f"source_{i}" for i in range(providers - 2))]
        self.definitions = {
            f"measure_{i}": score_plugin(name=f"measure_{i}") for i in range(metrics)
        }
        self.definitions["routes"] = PluginDefinition(
            resources={name: provider(name) for name in names},
            location_operations=[
                conversion(f"select_{i}", name, "native")
                for i, name in enumerate(names[2:])
            ],
        )

    def time_compile(self, _providers: int, _metrics: int) -> None:
        """Compile live definitions and project every accepted selection."""
        compile_live_catalog(self.definitions)


class DiscoverySuite:
    """Metric discovery across representative provider counts."""

    version = "2"
    params: ClassVar[list[int]] = [10, 100]
    param_names: ClassVar[list[str]] = ["editions"]

    def setup(self, editions: int) -> None:
        """Compile declarations outside timed projection calls."""
        self.discovery = discovery(editions)

    def time_list(self, _editions: int) -> None:
        """List compact metric summaries."""
        self.discovery.search()

    def time_search(self, _editions: int) -> None:
        """Search accepted source types."""
        self.discovery.search("places")

    def time_inspect(self, _editions: int) -> None:
        """Inspect one public metric."""
        self.discovery.inspect("measure_0")

    def track_list_bytes(self, _editions: int) -> int:
        """Return serialized metric listing size."""
        return sum(
            len(item.model_dump_json().encode()) for item in self.discovery.search()
        )

    def track_inspection_bytes(self, _editions: int) -> int:
        """Return serialized executable inspection size."""
        return len(self.discovery.inspect("measure_0").model_dump_json().encode())
