"""Catalog construction and steady-state discovery scaling."""

import json
from typing import ClassVar

from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.graph import build_graph
from lyra.server.plugins import InstalledPlugin
from test_support.edition_catalog import discovery, edition_manifests
from test_support.workloads import catalog, catalog_declarations, workload


class CatalogSuite:
    """Compile a shared-source graph and its public registry."""

    version = "2"
    params: ClassVar[list[int]] = [10, 100, 1000]
    param_names: ClassVar[list[str]] = ["nodes"]

    def setup_cache(self) -> dict[int, list[InstalledPlugin]]:
        """Return serialized declarations shared across measurement processes."""
        return catalog_declarations(self.params)

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
        """Compile contracts and capture application routing."""
        catalog(self.installed)

    def time_discovery(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> None:
        """Build and serialize a public catalog."""
        json.dumps(
            [
                item.model_dump(mode="json")
                for item in self.catalog.discovery.search(limit=_nodes)
            ]
        )

    def track_catalog_bytes(
        self, _declarations: dict[int, list[InstalledPlugin]], _nodes: int
    ) -> int:
        """Return UTF-8 public discovery size."""
        return len(
            json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in self.catalog.discovery.search(limit=_nodes)
                ]
            ).encode()
        )

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
        self.manifests = edition_manifests(editions)

    def time_compile(self, _editions: int) -> None:
        """Measure full catalog compilation including accepted source projection."""
        compile_catalog(self.manifests)


class DiscoverySuite:
    """Shared family projections across representative edition counts."""

    params: ClassVar[list[int]] = [10, 100]
    param_names: ClassVar[list[str]] = ["editions"]

    def setup(self, editions: int) -> None:
        """Compile declarations outside timed projection calls."""
        self.discovery = discovery(editions)

    def time_list(self, _editions: int) -> None:
        """List compact families."""
        self.discovery.search()

    def time_search(self, _editions: int) -> None:
        """Search accepted source types."""
        self.discovery.search("places")

    def time_inspect(self, _editions: int) -> None:
        """Inspect one executable edition."""
        self.discovery.inspect("measure", edition="0")

    def track_list_bytes(self, _editions: int) -> int:
        """Return serialized family discovery size."""
        return sum(
            len(item.model_dump_json().encode()) for item in self.discovery.search()
        )

    def track_inspection_bytes(self, _editions: int) -> int:
        """Return serialized executable inspection size."""
        return len(
            self.discovery.inspect("measure", edition="0").model_dump_json().encode()
        )
