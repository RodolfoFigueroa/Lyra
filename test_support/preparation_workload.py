"""Public input preparation and persistent identity scaling."""

import pandas as pd
from pydantic import Field

from lyra.contracts.locations import LocationContract
from lyra.data.locations import Locations
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.workloads import frame, schema


class Parameters(MetricParameters):
    """Ordinary bounded caller parameters used alongside selections."""

    weight: int = Field(default=1, ge=1, le=10, description="Calculation weight")


def preparation_workload(ids: int) -> tuple[CompiledCatalog, dict]:
    """Build a two-input metric catalog and its public request."""

    @metric(
        name="root",
        locations={
            key: LocationContract(
                family=LocationOutputSchema(schema(frame(1, "polygon"))).family(
                    "bench:points", "point"
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
    def root(left: Locations, right: Locations, parameters: Parameters) -> pd.DataFrame:
        return frame((len(left) + len(right)) * parameters.weight)

    plugin = PluginDefinition(metrics=[root])
    selection = {
        "collection": "bench:points",
        "unit_type": "point",
        "ids": [str(i) for i in range(ids)],
    }
    catalog = LocalRunner.from_definitions({"bench": plugin}).catalog
    return catalog, {
        "metric": "root",
        "parameters": {"weight": 2},
        "inputs": {"left": selection, "right": selection},
    }
