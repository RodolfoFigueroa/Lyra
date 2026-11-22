"""SDK declarations for discovery over concrete measurement providers."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.routing_plugins import conversion, provider


class Measurement(MetricParameters):
    count: int = Field(default=2, ge=1, le=9, description="Measurement count")


def discovery(
    count: int = 2, *, private: int | None = None, regional: bool = False
) -> Discovery:
    """Compile a measurement for each provider and optionally route the first."""
    metrics = []
    resources = {}
    for index in range(count):
        name = f"measurement_{index}"
        resources[name] = provider(name)

        def measure(places: gpd.GeoDataFrame, parameters: Measurement) -> pd.DataFrame:
            del places, parameters
            return pd.DataFrame()

        metrics.append(
            metric(
                name=f"measure_{index}",
                description="Measurement",
                code_revision="1",
                private=index == private,
                inputs={"places": LocationDependency(provider=name)},
                outputs={"value": pa.DataFrameSchema(strict=True)},
            )(measure)
        )
    operations = []
    if regional:
        resources["regional"] = provider("regional")
        operations.append(conversion("convert", "regional", "measurement_0"))
    return Discovery(
        compile_live_catalog(
            {
                "benchmark": PluginDefinition(
                    metrics=metrics, resources=resources, location_operations=operations
                )
            }
        )
    )
