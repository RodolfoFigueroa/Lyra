"""Resolve exact city records from bundled package data."""

import geopandas as gpd
from typing_extensions import override

from city_example.data import block_frame, load_city, street_frame
from city_example.schemas import BLOCK_SCHEMA, BLOCKS, STREET_SCHEMA, STREETS
from lyra.contracts.errors import LocationAcquisitionError
from lyra.sdk import LocationProvider, Origin

CITY_ORIGIN = Origin(id="example-city", revision="fixture_1")


# docs:start provider
class CityStreetProvider(LocationProvider):
    """Expose the bundled street geometry and condition observations."""

    id = STREETS
    code_revision = "1"
    origins = (CITY_ORIGIN,)
    schema = STREET_SCHEMA

    @override
    def setup(self) -> None:
        """Read the bundle only when street records are needed.

        Raises:
            LocationAcquisitionError: If the bundle has a different declared revision.
        """
        city = load_city()
        if city.revision != CITY_ORIGIN.revision:
            message = "The bundled city revision differs from its declared origin"
            raise LocationAcquisitionError(message)
        self.frame = street_frame(city)

    @override
    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return requested IDs present in the bundled street survey."""
        return set(ids).intersection(self.frame.location_id)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return selected streets for automatic exact-membership validation."""
        with self.operation("resolve"):
            return self.frame.loc[self.frame.location_id.isin(ids)].copy()


class CityBlockProvider(LocationProvider):
    """Expose block boundaries and complete declared street relationships."""

    id = BLOCKS
    code_revision = "1"
    origins = (CITY_ORIGIN,)
    schema = BLOCK_SCHEMA

    @override
    def setup(self) -> None:
        """Read the bundle only when block records are needed.

        Raises:
            LocationAcquisitionError: If the bundle has a different declared revision.
        """
        city = load_city()
        if city.revision != CITY_ORIGIN.revision:
            message = "The bundled city revision differs from its declared origin"
            raise LocationAcquisitionError(message)
        self.frame = block_frame(city)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return selected blocks with their fixed relationship attributes."""
        with self.operation("resolve"):
            return self.frame.loc[self.frame.location_id.isin(ids)].copy()


# docs:end provider
