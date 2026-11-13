"""Native record schema and in-memory provider for smoke metrics."""

import geopandas as gpd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from typing_extensions import override

from lyra.sdk import LocationProvider, Origin

SCHEMA = pa.DataFrameSchema(
    {
        "location_id": pa.Column(
            str,
            unique=True,
            checks=pa.Check(lambda values: values.str.len().gt(0), name="nonempty"),
            description="Area identifier",
            metadata={"unit": None},
        ),
        "geometry": pa.Column(
            Geometry(crs="EPSG:4326"),
            checks=pa.Check(
                lambda values: values.is_valid & ~values.is_empty, name="valid_geometry"
            ),
            description="Area geometry",
            metadata={"unit": None},
        ),
    },
    strict=True,
)
SUBJECT = "smoke_areas"


class SmokeLocationProvider(LocationProvider):
    """Resolve the tutorial's fixed polygon without external services."""

    id = SUBJECT
    code_revision = "1"
    origins = (Origin(id="smoke-fixture", revision="1"),)
    schema = SCHEMA

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return requested fixture records for exact-membership validation."""
        frame = gpd.GeoDataFrame(
            {"location_id": ["area-1"]},
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
        return frame.loc[frame.location_id.isin(ids)].copy()
