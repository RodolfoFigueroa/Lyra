"""Public input preparation and persistent identity scaling."""

from typing import ClassVar

from lyra.runtime.planning.computation import compile_computation
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from test_support.accepted_sources import declarations
from test_support.location_operations import Parameters
from test_support.manifest_catalog import compile_catalog
from test_support.manifest_graph import PluginGraph, build_graph
from test_support.parameter_catalog import with_models
from test_support.plugin_graph import manifest
from test_support.preparation_workload import preparation_workload
from test_support.workloads import workload


class PreparationSuite:
    """Prepare named native selections without acquiring location data."""

    version = "2"
    params: ClassVar[list[int]] = [1, 1000]

    def setup(self, ids: int) -> None:
        """Declare two inputs and deterministic public selections."""
        self.catalog, self.request = preparation_workload(ids)

    def time_prepare(self, _ids: int) -> None:
        """Validate and bind two named selections."""
        prepare_execution(self.catalog, self.request)

    def time_reject(self, _ids: int) -> None:
        """Reject an unexpected public field before acquisition.

        Raises:
            RuntimeError: If malformed input is unexpectedly accepted.
        """
        try:
            prepare_execution(self.catalog, self.request | {"unexpected": 1})
        except PreparationError:
            return
        message = "Invalid benchmark input was accepted"
        raise RuntimeError(message)


class IdentitySuite:
    """Measure compiled dependency identities independently of data hashing."""

    version = "2"
    params: ClassVar[list[int]] = [10, 1000]

    def setup_cache(self) -> dict[int, PluginGraph]:
        """Build metadata graphs once in the ASV cache process.

        Returns:
            Metadata graphs indexed by node count.
        """
        return {
            nodes: build_graph(
                {
                    "benchmark-fixture": workload(
                        1, "catalog", nodes
                    ).plugin.node_manifests
                }
            )
            for nodes in self.params
        }

    def setup(self, graphs: dict[int, PluginGraph], nodes: int) -> None:
        """Select a prepared graph outside timing."""
        self.graph = graphs[nodes]

    def time_compile_identity(
        self, _graphs: dict[int, PluginGraph], _nodes: int
    ) -> None:
        """Compile the production computation and revision identities."""
        compile_computation(self.graph, {})


class CatalogPreparationSuite:
    """Measure shared acceptance independently of acquisition and execution."""

    version = "1"
    params: ClassVar[list[str]] = ["native", "routed", "rejected"]

    def setup(self, scenario: str) -> None:
        """Compile a two-input catalog before timing acceptance."""
        node, source, other, operations = declarations()
        node.location_inputs["places"].source_bindings = (source.reference,)
        self.catalog = compile_catalog(
            {"benchmark": manifest("benchmark", node, *operations)}
        )
        self.catalog = with_models(
            self.catalog, {op.name: Parameters for op in operations}
        )
        selected = (
            node.location_inputs["places"].contract if scenario == "native" else source
        )
        self.request = {
            "metric": "score",
            "inputs": {
                name: {
                    "provider": binding.family.collection,
                    "ids": ["1"],
                }
                for name, binding in (("places", selected), ("reference", other))
            },
        }
        if scenario == "rejected":
            self.request["metric"] = "missing"

    def time_prepare(self, scenario: str) -> None:
        """Bind requests or reject them without resolving IDs.

        Raises:
            PreparationError: If a valid workload is rejected.
        """
        try:
            prepare_execution(self.catalog, self.request)
        except PreparationError:
            if scenario != "rejected":
                raise
