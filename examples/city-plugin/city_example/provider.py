"""Resolve exact city IDs from bundled package data."""

from typing import ClassVar

import geopandas as gpd
from lyra.sdk import (
    IOConfig,
    LocationAcquisitionError,
    LocationCollection,
    LocationContract,
    LocationOutputSchema,
    LocationProvider,
    UnresolvedLocationIDsError,
)
from typing_extensions import override

from city_example.data import block_frame, load_city, street_frame
from city_example.schemas import BLOCK_SCHEMA, BLOCKS, STREET_SCHEMA, STREETS


# docs:start provider
class CityLocationProvider(LocationProvider[IOConfig]):
    """Resolve streets and blocks in the synthetic city."""

    config_model = IOConfig
    collections = (
        LocationCollection(
            name="example:city", unit_types=("street", "block"), crs="EPSG:32614"
        ),
    )
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        ("example:city", "street"): STREET_SCHEMA,
        ("example:city", "block"): BLOCK_SCHEMA,
    }
    contracts = (STREETS, BLOCKS)
    # docs:end provider

    @override
    def setup(self) -> None:
        """Load data only when an execution needs the provider."""
        city = load_city()
        self.revision = city.revision
        self.frames = {"street": street_frame(city), "block": block_frame(city)}

    @override
    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        """Resolve exactly the requested IDs in request order.

        Returns:
            Complete geometry and properties under the selected contract.

        Raises:
            LocationAcquisitionError: If the bundle cannot serve the pinned revision.
            UnresolvedLocationIDsError: If any ID is unknown for this unit type.
        """
        if contract.data_revision != self.revision:
            message = "The bundled city cannot serve the requested data revision"
            raise LocationAcquisitionError(
                message,
                collection=contract.family.collection,
                unit_type=contract.family.unit_type,
                ids=ids,
            )
        frame = self.frames[contract.family.unit_type].set_index("id", drop=False)
        missing = [identifier for identifier in ids if identifier not in frame.index]
        if missing:
            raise UnresolvedLocationIDsError(
                contract.family.collection, contract.family.unit_type, missing
            )
        with self.operation("resolve"):
            return frame.loc[ids].reset_index(drop=True)
