"""Reusable location resolution fixtures."""

from typing import ClassVar

import geopandas as gpd
from shapely.geometry import Point

from lyra.contracts.locations import LocationContract, LocationSelection
from lyra.contracts.providers import LocationCollection, LocationProviderManifest
from lyra.contracts.resources import IOConfig
from lyra.data.locations import Locations
from lyra.runtime.locations import resolve_locations
from lyra.runtime.planning.providers import SelectedLocationProvider
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    producer_result,
    scoped_payload,
)

COLLECTIONS = (
    LocationCollection(name="test:units", unit_types=("lot", "block"), crs="EPSG:4326"),
    LocationCollection(name="test:roads", unit_types=("road",), crs="EPSG:27700"),
)


def selection(
    *ids: str, collection: str = "test:units", unit_type: str = "lot"
) -> LocationSelection:
    return LocationSelection(collection=collection, unit_type=unit_type, ids=list(ids))


def features(ids: list[str], crs: str = "EPSG:4326") -> Locations:
    return fixture_locations(
        scoped_payload(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": unit_id,
                        "geometry": {"type": "Point", "coordinates": [1, 2]},
                        "properties": {"original": unit_id},
                    }
                    for unit_id in ids
                ],
                "crs": {"type": "name", "properties": {"name": crs}},
            }
        ),
        schema=output_schema(original=str),
    )


def frame_features(ids: list[str], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ids, "original": ids}, geometry=[Point(1, 2)] * len(ids), crs=crs
    )


class Provider(LocationProvider[IOConfig]):
    collections = COLLECTIONS
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        (item.name, unit): output_schema(original=str)
        for item in COLLECTIONS
        for unit in item.unit_types
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

    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        collection, unit_type = contract.family.collection, contract.family.unit_type
        assert unit_type in {"lot", "block", "road"}
        crs = next(item.crs for item in self.collections if item.name == collection)
        return producer_result(
            fixture_locations(
                features(list(reversed(ids)), crs).to_dict()
                | {"collection": collection, "unit_type": unit_type},
                schema=output_schema(original=str),
            ),
            self.outputs[collection, unit_type],
        )


SELECTED = SelectedLocationProvider(
    "test-plugin",
    LocationProviderManifest(
        resource="locations", collections=COLLECTIONS, contracts=Provider.contracts
    ),
)


def resolve(inputs: dict[str, LocationSelection]) -> dict[str, Locations]:
    return resolve_locations(
        inputs, selected=SELECTED, resources={"locations": Provider}
    )
