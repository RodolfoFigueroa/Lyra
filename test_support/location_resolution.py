"""Native in-process providers and resource-owned resolution fixtures."""

import geopandas as gpd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.locations import LocationSelection
from lyra.contracts.providers import ProviderDeclaration
from lyra.data.locations import Locations
from lyra.runtime.locations import resolve_locations
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.planning.providers import SelectedLocationProvider
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    scoped_payload,
)
from test_support.location_schema import column


def provider_schema(crs: str = "EPSG:4326") -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "location_id": column(
                str, unique=True, checks=pa.Check.str_length(min_value=1)
            ),
            "geometry": column(
                Geometry(crs=crs),
                checks=pa.Check(
                    lambda values: values.is_valid & ~values.is_empty,
                    name="valid_geometry",
                ),
            ),
            "original": column(str),
        },
        strict=True,
    )


def selection(*ids: str, provider: str = "test_units") -> LocationSelection:
    return LocationSelection(provider=provider, ids=list(ids))


def features(ids: list[str], crs: str = "EPSG:4326") -> Locations:
    return fixture_locations(
        scoped_payload(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": identifier,
                        "geometry": {"type": "Point", "coordinates": [1, 2]},
                        "properties": {"original": identifier},
                    }
                    for identifier in ids
                ],
                "crs": {"type": "name", "properties": {"name": crs}},
            }
        ),
        schema=output_schema(original=str),
    )


def frame_features(ids: list[str], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"location_id": ids, "original": ids},
        geometry=[Point(1, 2)] * len(ids),
        crs=crs,
    )


class Provider(LocationProvider):
    id = "test_units"
    schema = provider_schema()

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return frame_features(list(reversed(ids)))


class Blocks(Provider):
    id = "test_blocks"


class Roads(Provider):
    id = "test_roads"
    schema = provider_schema("EPSG:27700")

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return frame_features(list(reversed(ids)), "EPSG:27700")


RESOURCES = {"locations": Provider, "blocks": Blocks, "roads": Roads}
PROVIDERS = compile_provider_registry({"test": PluginDefinition(resources=RESOURCES)})
SELECTED = SelectedLocationProvider(
    "test-plugin",
    ProviderDeclaration(resource="locations", contract=PROVIDERS[Provider.id].contract),
)


def resolve(inputs: dict[str, LocationSelection]) -> dict[str, Locations]:
    return resolve_locations(inputs, providers=PROVIDERS, resources=RESOURCES)


def location_value(*ids: str, provider: str = Provider.id) -> Locations:
    registered = PROVIDERS[provider]
    schema = LocationOutputSchema(registered.schema.schema, id_column="location_id")
    return schema.build_contract(
        frame_features(
            list(ids), str(registered.schema.schema.columns["geometry"].dtype.crs)
        ),
        registered.contract,
    )
