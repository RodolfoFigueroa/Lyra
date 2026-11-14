"""Representative in-memory SDK operations, measured independently."""

from typing import ClassVar

import geopandas as gpd
import pandera.pandas as pa
from geopandas import GeoDataFrame
from pandera.engines.geopandas_engine import Geometry

from lyra.sdk.location_schema import LocationOutputSchema


class LocationsSuite:
    """Construct and export point locations."""

    params: ClassVar[list[int]] = [10_000, 100_000]
    param_names: ClassVar[list[str]] = ["rows"]

    def setup(self, rows: int) -> None:
        """Prepare deterministic inputs outside timing."""
        self.frame = gpd.GeoDataFrame(
            {
                "id": [str(i) for i in range(rows)],
                "value": [float(i) for i in range(rows)],
            },
            geometry=gpd.points_from_xy([1.0] * rows, [2.0] * rows),
            crs="EPSG:4326",
        )
        self.schema = LocationOutputSchema(
            pa.DataFrameSchema(
                {
                    name: pa.Column(
                        dtype,
                        description=name,
                        metadata={"unit": None},
                        checks=pa.Check(lambda s: s.is_valid, name="valid_geometry")
                        if name == "geometry"
                        else None,
                    )
                    for name, dtype in {
                        "id": str,
                        "value": float,
                        "geometry": Geometry(crs=4326),
                    }.items()
                },
                strict=True,
            )
        )
        self.locations = self._construct()

    def _construct(self) -> GeoDataFrame:
        return self.schema.prepare_frame(self.frame)

    def time_construct(self, _rows: int) -> None:
        """Measure construction including production validation."""
        self._construct()

    def peakmem_construct(self, _rows: int) -> None:
        """Measure total process peak during construction."""
        self._construct()

    def time_export(self, _rows: int) -> None:
        """Measure dataframe export."""
        self.locations.copy(deep=True)

    def peakmem_export(self, _rows: int) -> None:
        """Measure total process peak during export."""
        self.locations.copy(deep=True)
