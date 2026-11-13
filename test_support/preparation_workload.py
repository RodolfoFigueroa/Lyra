"""Public input preparation and persistent identity scaling."""

import geopandas as gpd
import pandas as pd
from pydantic import Field

from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.sdk import LocationDependency, PluginDefinition, metric
from lyra.sdk.parameters import MetricParameters
from test_support.location_workloads import Provider
from test_support.workloads import frame, schema


class Parameters(MetricParameters):
    """Ordinary bounded caller parameters used alongside selections."""

    weight: int = Field(default=1, ge=1, le=10, description="Calculation weight")


def preparation_workload(ids: int) -> tuple[CompiledCatalog, dict]:
    """Build a two-input metric catalog and its public request."""

    @metric(
        code_revision="1",
        name="root",
        inputs={
            key: LocationDependency(provider=Provider.id) for key in ("left", "right")
        },
        description="Two selections",
        outputs={"root": schema(frame(1))},
    )
    def root(
        left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, parameters: Parameters
    ) -> pd.DataFrame:
        return frame((len(left) + len(right)) * parameters.weight)

    plugin = PluginDefinition(metrics=[root], resources={"units": Provider})
    selection = {
        "provider": Provider.id,
        "ids": [str(i) for i in range(ids)],
    }
    catalog = LocalRunner.from_definitions({"bench": plugin}).catalog
    return catalog, {
        "metric": "root",
        "parameters": {"weight": 2},
        "inputs": {"left": selection, "right": selection},
    }
