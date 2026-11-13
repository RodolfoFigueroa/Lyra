from typing import ClassVar

import geopandas as gpd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.resources import IOConfig
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition
from test_support.location_schema import column


class ProviderSettings(IOConfig):
    label: str


class FakeProvider(LocationProvider[ProviderSettings]):
    config_model = ProviderSettings
    secret_names = ("token",)
    id = "observatory_features"
    origins = ()
    schema = pa.DataFrameSchema(
        {
            "location_id": column(
                str, unique=True, checks=pa.Check.str_length(min_value=1)
            ),
            "geometry": column(
                Geometry(crs="EPSG:27700"),
                checks=pa.Check(
                    lambda value: value.is_valid & ~value.is_empty,
                    name="valid_geometry",
                ),
            ),
            "label": column(str),
        },
        strict=True,
    )
    events: ClassVar[list[str]] = []

    @override
    def setup(self) -> None:
        assert dict(self.secrets) == {"token": "fake-token"}
        self.events.append("setup")

    @override
    def close(self) -> None:
        self.events.append("close")

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        with self.operation("resolve"):
            found = [identifier for identifier in ids if identifier in {"001", "a/b"}]
            return gpd.GeoDataFrame(
                {"location_id": found, "label": [self.config.label] * len(found)},
                geometry=[Point(1, 2)] * len(found),
                crs="EPSG:27700",
            )


def create_provider() -> PluginDefinition:
    return PluginDefinition(resources={"places": FakeProvider})
