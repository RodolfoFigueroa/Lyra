"""Catalog construction and steady-state discovery scaling."""

from typing import ClassVar

from lyra.sdk import LocalRunner
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.graph import build_graph
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import NodeManifest, PluginInfo, PluginManifest
from lyra.sdk.parameters import build_request_schema

from benchmarks.locations import Provider
from benchmarks.workloads import catalog, installed, workload
from lyra_app.plugins import InstalledPlugin


class CatalogSuite:
    """Compile a shared-source graph and its public registry."""

    version = "1"
    params: ClassVar[list[int]] = [10, 100, 1000]
    param_names: ClassVar[list[str]] = ["nodes"]

    def setup_cache(self) -> dict[int, list[InstalledPlugin]]:
        """Return serialized declarations shared across measurement processes."""
        return {
            nodes: installed(workload(1, "catalog", nodes).plugin)
            for nodes in self.params
        }

    def setup(self, declarations: dict[int, list[InstalledPlugin]], nodes: int) -> None:
        """Prepare the catalog from reusable declaration fixtures."""
        self.installed = declarations[nodes]
        self.manifests = {
            item.distribution: item.manifest.nodes for item in self.installed
        }
        self.catalog = catalog(self.installed)

    def time_graph(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Validate graph dependencies."""
        build_graph(self.manifests)

    def time_catalog(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Compile registry, graph, validators, and fingerprints."""
        catalog(self.installed)

    def time_discovery(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Build and serialize a public catalog."""
        self.catalog.get_metric_catalog().model_dump_json()

    def track_catalog_bytes(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> int:
        """Return UTF-8 public discovery size."""
        return len(self.catalog.get_metric_catalog().model_dump_json().encode())

    def peakmem_compile(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
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
    """Compile native source selections across concrete edition families."""

    version = "1"
    params: ClassVar[list[int]] = [10, 100]
    param_names: ClassVar[list[str]] = ["editions"]

    def setup(self, editions: int) -> None:
        """Build edition declarations with distinct concrete source identities."""
        nodes = []
        for index in range(editions):
            native = Provider.contracts[0].model_copy(update={"edition": str(index)})
            nodes.append(
                NodeManifest(
                    name=f"measure_{index}",
                    family="measure",
                    edition=str(index),
                    description="Measurement",
                    outputs={"value": OutputMetadata(columns=[])},
                    request_schema=build_request_schema("measure", None, {"places"}),
                    spatial_inputs={"places": "location"},
                    location_contracts={"places": native},
                )
            )
        self.manifests = {
            "benchmark": PluginManifest(
                plugin=PluginInfo(name="benchmark", version="1"),
                factory="benchmarks.catalog:AcceptedSourcesSuite",
                nodes=nodes,
            )
        }

    def time_compile(self, _editions: int) -> None:
        """Measure full catalog compilation including accepted source projection."""
        compile_catalog(self.manifests)
