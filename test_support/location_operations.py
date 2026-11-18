"""Reusable pure location operation fixtures."""

import geopandas as gpd
from pydantic import Field
from shapely.geometry import Point
from typing_extensions import override

from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.locations import LocationProvider
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation
from lyra.sdk.resources import BaseIO
from test_support.location_schema import column, contract, declaration
from test_support.native_providers import native_schema


class Parameters(MetricParameters):
    count: int = Field(default=2, description="Record count")


class MemoryIO(BaseIO):
    def setup(self) -> None:
        pass

    def close(self) -> None:
        pass


OUTPUT = contract(declaration(count=column(int, coerce=True)))


class Places(LocationProvider):
    id = "example:places"
    schema = native_schema(OUTPUT, "EPSG:4326")

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"location_id": ids, "count": [4] * len(ids)},
            geometry=[Point(1, 2)] * len(ids),
            crs="EPSG:4326",
        )


class CatalogPlaces(Places):
    """Every requested ID has a synthetic base record in this provider."""

    @override
    def existing_ids(self, ids: list[str]) -> set[str]:
        return set(ids)


@location_operation(
    code_revision="1",
    inputs={"source": LocationDependency(provider=Places.id)},
    target_provider=Places.id,
    name="select_places",
    description="Select places using supplied records.",
)
def select_places(
    source: gpd.GeoDataFrame,
    *,
    parameters: Parameters,
) -> dict[str, tuple[str, ...]]:
    return {key: (key,) for key in source.location_id.iloc[: parameters.count]}


def definition() -> PluginDefinition:
    return PluginDefinition(
        location_operations=[select_places], resources={"places": Places}
    )
