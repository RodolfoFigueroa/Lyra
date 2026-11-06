"""Public input preparation and persistent identity scaling."""

from typing import ClassVar

from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.computation import compile_computation
from lyra.runtime.planning.graph import build_graph
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from test_support.accepted_sources import declarations
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

    def setup(self, nodes: int) -> None:
        """Compile declarations before deriving their identity."""
        self.graph = build_graph(
            {"benchmark-fixture": workload(1, "catalog", nodes).plugin.node_manifests}
        )

    def time_compile_identity(self, _nodes: int) -> None:
        """Compile the production computation and revision identities."""
        compile_computation(self.graph, None)


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
        selected = (
            node.location_inputs["places"].contract if scenario == "native" else source
        )
        self.request = {
            "metric": "score",
            "inputs": {
                name: {
                    "collection": binding.family.collection,
                    "unit_type": binding.family.unit_type,
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
