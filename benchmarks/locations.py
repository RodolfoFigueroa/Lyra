"""Batch acquisition and explicit location-operation execution."""

from typing import ClassVar

import geopandas as gpd
import pandas as pd
from lyra.sdk import (
    Dependency,
    IOConfig,
    LocalRunner,
    LocationCollection,
    LocationDependency,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    PluginDefinition,
    location_operation,
    metric,
)
from typing_extensions import override

from benchmarks.workloads import frame, schema


class Provider(LocationProvider[IOConfig]):
    """Provide deterministic batches in memory without a service client."""

    collections = (
        LocationCollection(
            name="bench:units", unit_types=("polygon",), crs="EPSG:4326"
        ),
    )
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        ("bench:units", "polygon"): LocationOutputSchema(schema(frame(1, "polygon")))
    }

    @override
    def resolve(
        self, collection: str, unit_type: str, ids: list[str]
    ) -> gpd.GeoDataFrame:
        result = frame(len(ids), "polygon")
        result["id"] = ids
        return gpd.GeoDataFrame(result)


class LocationExecutionSuite:
    """Resolve a public batch and execute an explicitly bound location operation."""

    version = "1"
    params: ClassVar[list[int]] = [1, 1000]

    def setup(self, rows: int) -> None:
        """Declare the provider, selector, and consuming calculation."""

        @location_operation(
            name="select",
            description="Select native polygons",
            output=Provider.outputs["bench:units", "polygon"],
            collection="bench:units",
            unit_type="polygon",
        )
        def select() -> gpd.GeoDataFrame:
            return gpd.GeoDataFrame(frame(rows, "polygon"))

        @metric(
            name="calculate",
            description="Calculate native rows",
            outputs={"calculate": schema(frame(1))},
        )
        def calculate(native: Locations) -> pd.DataFrame:
            return frame(len(native))

        @metric(
            name="root",
            description="Calculate over acquired and derived batches",
            inputs={
                "derived": Dependency(
                    "calculate", locations={"native": LocationDependency("select")}
                )
            },
            outputs={"root": schema(frame(1))},
        )
        def root(native: Locations, derived: pd.DataFrame) -> pd.DataFrame:
            return frame(len(native) + len(derived))

        plugin = PluginDefinition(
            metrics=[root, calculate],
            location_operations=[select],
            resources={"locations": Provider},
            location_provider="locations",
        )
        self.runner = LocalRunner.from_definitions({"benchmark-fixture": plugin})
        self.inputs = {
            "native": {
                "collection": "bench:units",
                "unit_type": "polygon",
                "ids": [str(i) for i in range(rows)],
            }
        }

    def time_local(self, _rows: int) -> None:
        """Include acquisition, operation execution, and result validation."""
        self.runner.run("root", self.inputs)

    def peakmem_local(self, _rows: int) -> None:
        """Measure whole-process memory during batch execution."""
        self.runner.run("root", self.inputs)
