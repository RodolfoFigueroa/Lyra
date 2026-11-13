"""Batch acquisition and explicit location-operation execution."""

import geopandas as gpd
import pandas as pd
from typing_extensions import override

from lyra.contracts.dependencies import Input
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.native_providers import native_schema
from test_support.workloads import frame, schema


class Provider(LocationProvider):
    """Provide deterministic batches in memory without a service client."""

    id = "bench_units"
    code_revision = "1"
    schema = native_schema(
        LocationOutputSchema(schema(frame(1, "polygon"))), "EPSG:4326"
    )

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        result = frame(len(ids), "polygon").rename(columns={"id": "location_id"})
        result["location_id"] = ids
        return gpd.GeoDataFrame(result)


def location_workload(rows: int) -> tuple[LocalRunner, dict]:
    """Bind a polygon provider and selector to a calculation over both batches."""

    @location_operation(
        code_revision="1",
        name="select",
        description="Select native polygons",
        inputs={"native": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def select(native: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in native.location_id}

    @metric(
        code_revision="1",
        name="calculate",
        inputs={"native": LocationDependency(provider=Provider.id)},
        description="Calculate native rows",
        outputs={"calculate": schema(frame(1))},
    )
    def calculate(native: gpd.GeoDataFrame) -> pd.DataFrame:
        return frame(len(native))

    @metric(
        code_revision="1",
        name="root",
        description="Calculate over acquired and derived batches",
        inputs={
            "native": LocationDependency(provider=Provider.id),
            "derived": Dependency("calculate", producer="calculate").bind(
                native=LocationDependency("select").bind(native=Input("native"))
            ),
        },
        outputs={"root": schema(frame(1))},
    )
    def root(native: gpd.GeoDataFrame, derived: pd.DataFrame) -> pd.DataFrame:
        return frame(len(native) + len(derived))

    plugin = PluginDefinition(
        metrics=[root, calculate],
        location_operations=[select],
        resources={"locations": Provider},
    )
    runner = LocalRunner.from_definitions({"benchmark-fixture": plugin})
    inputs = {"native": {"provider": Provider.id, "ids": [str(i) for i in range(rows)]}}
    return runner, inputs
