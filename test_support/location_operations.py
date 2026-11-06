"""Reusable location operations fixtures."""

from typing import Annotated

import geopandas as gpd
import pandas as pd
from pydantic import Field

from lyra.contracts.dependencies import Input
from lyra.data.locations import Locations
from lyra.sdk.context import LocationContext
from lyra.sdk.dependencies import Dependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation
from lyra.sdk.resources import BaseIO, Resource
from test_support.location_helpers import node_contract, producer_result
from test_support.location_schema import column, contract, declaration


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Record count")


class MemoryIO(BaseIO):
    def setup(self) -> None:
        pass

    def close(self) -> None:
        pass


OUTPUT = contract(declaration(count=column(int, coerce=True)))


@location_operation(
    locations={"source": node_contract("example:places", "point", OUTPUT)},
    output_contract=node_contract("example:places", "point", OUTPUT),
    name="select_places",
    description="Select places using supplied records.",
    output=OUTPUT,
    inputs={
        "table": Dependency(
            "records",
            parameters={"count": Input("parameters.count")},
            producer="records",
        )
    },
)
def select_places(
    source: Locations,
    parameters: Parameters,
    table: pd.DataFrame,
    memory: Annotated[MemoryIO, Resource("memory")],
    context: LocationContext,
) -> gpd.GeoDataFrame:
    assert isinstance(memory, MemoryIO)
    assert len(table) == parameters.count
    context.report_progress(stage="select", current=len(source.ids))
    return producer_result(
        context.resolve_locations(
            source.collection, source.unit_type, list(source.ids)
        ),
        OUTPUT,
    )


def definition() -> PluginDefinition:
    return PluginDefinition(
        location_operations=[select_places], resources={"memory": MemoryIO}
    )
