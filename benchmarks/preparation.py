"""Public input preparation and persistent identity scaling."""

from typing import ClassVar

import pandas as pd
from lyra.sdk import (
    LocationContract,
    LocationFamily,
    LocationOutputSchema,
    Locations,
    MetricParameters,
    PluginDefinition,
    metric,
)
from lyra.sdk.computation import computation_namespace
from lyra.sdk.errors import MetricInputError
from lyra.sdk.graph import build_graph
from lyra.sdk.job_inputs import prepare_selection_input
from pydantic import Field

from benchmarks.workloads import frame, schema, workload


class Parameters(MetricParameters):
    """Ordinary bounded caller parameters used alongside selections."""

    weight: int = Field(default=1, ge=1, le=10, description="Calculation weight")


class PreparationSuite:
    """Prepare named native selections without acquiring location data."""

    version = "1"
    params: ClassVar[list[int]] = [1, 1000]

    def setup(self, ids: int) -> None:
        """Declare two inputs and deterministic public selections."""

        @metric(
            name="root",
            locations={
                key: LocationContract(
                    family=LocationFamily.from_schema(
                        "bench:points",
                        "point",
                        LocationOutputSchema(schema(frame(1, "polygon"))),
                    ),
                    source="benchmark",
                    geographical_edition="current",
                    meaning="Benchmark points",
                )
                for key in ("left", "right")
            },
            description="Two selections",
            outputs={"root": schema(frame(1))},
        )
        def root(
            left: Locations, right: Locations, parameters: Parameters
        ) -> pd.DataFrame:
            return frame((len(left) + len(right)) * parameters.weight)

        self.plugin = PluginDefinition(metrics=[root])
        selection = {
            "collection": "bench:points",
            "unit_type": "point",
            "ids": [str(i) for i in range(ids)],
        }
        self.inputs = {
            "left": selection,
            "right": selection,
            "parameters": {"weight": 2},
        }

    def time_prepare(self, _ids: int) -> None:
        """Validate and bind two named selections."""
        prepare_selection_input(self.plugin, "root", self.inputs)

    def time_reject(self, _ids: int) -> None:
        """Reject an unexpected public field before acquisition.

        Raises:
            RuntimeError: If malformed input is unexpectedly accepted.
        """
        try:
            prepare_selection_input(
                self.plugin, "root", self.inputs | {"unexpected": 1}
            )
        except MetricInputError:
            return
        message = "Invalid benchmark input was accepted"
        raise RuntimeError(message)


class IdentitySuite:
    """Measure structural namespace calculation independently of data hashing."""

    version = "1"
    params: ClassVar[list[int]] = [10, 1000]

    def setup(self, nodes: int) -> None:
        """Compile declarations before deriving their identity."""
        self.graph = build_graph(
            {"benchmark-fixture": workload(1, "catalog", nodes).plugin.node_manifests}
        )

    def time_namespace(self, _nodes: int) -> None:
        """Derive the production computation namespace."""
        computation_namespace(self.graph, None, None)
