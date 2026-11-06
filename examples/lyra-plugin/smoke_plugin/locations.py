"""Concrete location contract and in-memory provider for smoke metrics."""

from typing import ClassVar

import geopandas as gpd
import pandera.pandas as pa
from typing_extensions import override

from lyra.contracts.errors import UnresolvedLocationIDsError
from lyra.contracts.locations import LocationContract
from lyra.contracts.providers import LocationCollection
from lyra.sdk import IOConfig, LocationOutputSchema, LocationProvider

SCHEMA = LocationOutputSchema(
    pa.DataFrameSchema(
        {
            name: pa.Column(dtype, description=name, metadata={"unit": None})
            for name, dtype in {"id": str, "geometry": "geometry"}.items()
        }
    )
)
SUBJECT = LocationContract(
    family=SCHEMA.family("test:units", "lot"),
    source="fixture",
    geographical_edition="current",
    meaning="Provider locations",
)


class SmokeLocationProvider(LocationProvider[IOConfig]):
    """Resolve the tutorial's fixed polygon without external services."""

    config_model = IOConfig
    collections = (
        LocationCollection(name="test:units", unit_types=("lot",), crs="EPSG:4326"),
    )
    outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
        ("test:units", "lot"): SCHEMA,
    }
    contracts = (SUBJECT,)

    @override
    def resolve(self, contract: LocationContract, ids: list[str]) -> gpd.GeoDataFrame:
        """Return the selected example polygon.

        Returns:
            The fixed geometry for ``area-1`` in the declared CRS.

        Raises:
            UnresolvedLocationIDsError: If any requested ID is unknown.
        """
        missing = [identifier for identifier in ids if identifier != "area-1"]
        if missing:
            raise UnresolvedLocationIDsError(
                contract.family.collection, contract.family.unit_type, missing
            )
        frame = gpd.GeoDataFrame(
            {"id": ["area-1"]},
            geometry=gpd.GeoSeries.from_wkt(
                [
                    (
                        "POLYGON ((-99.2 19.3, -99.1 19.3, "
                        "-99.1 19.4, -99.2 19.4, -99.2 19.3))"
                    )
                ]
            ),
            crs="EPSG:4326",
        )
        return frame.set_index("id", drop=False).loc[ids]
