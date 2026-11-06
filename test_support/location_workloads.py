"""Batch acquisition and explicit location-operation execution."""

from typing import ClassVar

import geopandas as gpd
import pandas as pd
from typing_extensions import override

from lyra.contracts.locations import LocationContract
from lyra.contracts.providers import LocationCollection
from lyra.contracts.resources import IOConfig
from lyra.data.locations import Locations
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.workloads import frame, schema


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
    contracts = tuple(
        LocationContract(
            family=schema.family(collection, unit),
            source="fixture",
            geographical_edition="current",
            meaning="Provider locations",
        )
        for (collection, unit), schema in outputs.items()
    )

    @override
    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        _collection, _unit_type = contract.family.collection, contract.family.unit_type
        result = frame(len(ids), "polygon")
        result["id"] = ids
        return gpd.GeoDataFrame(result)


def location_workload(rows: int) -> tuple[LocalRunner, dict]:
    """Bind a polygon provider and selector to a calculation over both batches."""

    @location_operation(
        name="select",
        description="Select native polygons",
        output=Provider.outputs["bench:units", "polygon"],
        output_contract=Provider.contracts[0],
    )
    def select() -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(frame(rows, "polygon"))

    @metric(
        name="calculate",
        locations={"native": Provider.contracts[0]},
        description="Calculate native rows",
        outputs={"calculate": schema(frame(1))},
    )
    def calculate(native: Locations) -> pd.DataFrame:
        return frame(len(native))

    @metric(
        name="root",
        locations={"native": Provider.contracts[0]},
        description="Calculate over acquired and derived batches",
        inputs={
            "derived": Dependency(
                "calculate",
                locations={"native": LocationDependency("select")},
                producer="calculate",
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
    runner = LocalRunner.from_definitions({"benchmark-fixture": plugin})
    inputs = {
        "native": {
            "collection": "bench:units",
            "unit_type": "polygon",
            "ids": [str(i) for i in range(rows)],
        }
    }
    return runner, inputs
