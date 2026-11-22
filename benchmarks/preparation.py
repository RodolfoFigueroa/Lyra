"""Public input preparation and persistent identity scaling."""

from copy import deepcopy
from typing import ClassVar

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.computation import compile_computation
from lyra.runtime.planning.graph import PluginGraph
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from test_support.accepted_sources import routed_request
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
        """Compile each live graph once.

        Returns:
            Serializable identity inputs indexed by node count.
        """
        graphs = {}
        for nodes in self.params:
            graph = compile_live_catalog(
                {"benchmark-fixture": workload(1, "catalog", nodes).plugin}
            ).graph
            # Edges share node entries. Omit executable handlers throughout the copy.
            handlers = {
                id(entry.implementation): None
                for entry in graph.nodes.values()
                if entry.implementation is not None
            }
            graphs[nodes] = deepcopy(graph, handlers)
        return graphs

    def setup(self, graphs: dict[int, PluginGraph], nodes: int) -> None:
        """Select the cached graph for this size."""
        self.graph = graphs[nodes]

    def time_compile_identity(
        self, _graphs: dict[int, PluginGraph], _nodes: int
    ) -> None:
        """Compile computation identities over a live graph."""
        compile_computation(self.graph, {})


class CatalogPreparationSuite:
    """Measure shared acceptance independently of acquisition and execution."""

    version = "2"
    params: ClassVar[list[str]] = ["native", "routed", "rejected"]

    def setup(self, scenario: str) -> None:
        """Compile a two-input catalog before timing acceptance."""
        self.catalog, self.request = routed_request()
        if scenario == "native":
            self.request["inputs"]["places"]["provider"] = "native"
        if scenario == "rejected":
            self.request["metric"] = "missing"

    def time_prepare(self, scenario: str) -> None:
        """Bind requests or reject them without resolving IDs.

        Raises:
            PreparationError: If a valid workload is rejected.
            RuntimeError: If the rejected scenario is accepted.
        """
        try:
            prepare_execution(self.catalog, self.request)
        except PreparationError:
            if scenario != "rejected":
                raise
        else:
            if scenario == "rejected":
                msg = "Invalid benchmark input was accepted"
                raise RuntimeError(msg)
