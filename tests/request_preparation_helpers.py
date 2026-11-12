"""Catalog-only acceptance captures routes and rejects malformed requests."""

import pandas as pd
import pandera.pandas as pa

from lyra.data.locations import Locations
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_operations import Parameters as OperationParameters
from test_support.parameter_catalog import with_models


def with_parameters(
    catalog: CompiledCatalog, model: type[MetricParameters]
) -> CompiledCatalog:
    """Attach a real parameter implementation to the routed contract fixture."""

    def handler(
        places: Locations, reference: Locations, parameters: MetricParameters
    ) -> pd.DataFrame:
        del places, reference, parameters
        return pd.DataFrame()

    handler.__annotations__["parameters"] = model
    node = catalog.graph.nodes["score"].node
    decorated = metric(
        code_revision="1",
        name="score",
        description="Score",
        outputs={"result": pa.DataFrameSchema({})},
        locations={name: item.contract for name, item in node.location_inputs.items()},
    )(handler)
    plugin = BoundPlugin(PluginDefinition(metrics=[decorated]))
    description = plugin.node_manifest("score")
    assert description is not None
    node.parameters_schema = description.parameters_schema

    return with_models(
        catalog,
        {
            "score": model,
            "select_0": OperationParameters,
            "select_1": OperationParameters,
        },
    )
