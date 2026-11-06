from typing import ClassVar

import geopandas as gpd

from lyra.contracts.errors import UnresolvedLocationIDsError
from lyra.contracts.locations import LocationContract
from lyra.contracts.providers import LocationCollection
from lyra.contracts.resources import IOConfig
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    producer_result,
    scoped_payload,
)

COLLECTION = LocationCollection(
    name="observatory:features", unit_types=("sensor", "road"), crs="EPSG:27700"
)


class ProviderSettings(IOConfig):
    label: str


class FakeProvider(LocationProvider[ProviderSettings]):
    config_model = ProviderSettings
    secret_names = ("token",)
    collections = (COLLECTION,)
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        (COLLECTION.name, unit): output_schema(label=str)
        for unit in COLLECTION.unit_types
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
    events: ClassVar[list[str]] = []

    def setup(self) -> None:
        assert dict(self.secrets) == {"token": "fake-token"}
        self.events.append("setup")

    def close(self) -> None:
        self.events.append("close")

    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        collection, unit_type = contract.family.collection, contract.family.unit_type
        with self.operation("resolve"):
            missing = [unit_id for unit_id in ids if unit_id not in {"001", "a/b"}]
            if missing:
                raise UnresolvedLocationIDsError(collection, unit_type, missing)
            return producer_result(
                fixture_locations(
                    scoped_payload(
                        {
                            "collection": collection,
                            "unit_type": unit_type,
                            "features": [
                                {
                                    "type": "Feature",
                                    "id": unit_id,
                                    "geometry": {
                                        "type": "Point",
                                        "coordinates": [1, 2],
                                    },
                                    "properties": {"label": self.config.label},
                                }
                                for unit_id in ids
                            ],
                            "crs": {
                                "type": "name",
                                "properties": {"name": COLLECTION.crs},
                            },
                        }
                    ),
                    schema=output_schema(label=str),
                ),
                self.outputs[collection, unit_type],
            )


def create_provider() -> PluginDefinition:
    return PluginDefinition(
        resources={"places": FakeProvider}, location_provider="places"
    )
