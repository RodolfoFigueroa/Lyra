"""Representative in-memory SDK operations, measured independently."""

from typing import ClassVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from lyra.sdk import LocationOutputSchema, Locations, ipc_codec
from lyra.sdk.typed_storage import Snapshot
from pandera.engines.geopandas_engine import Geometry


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
                    name: pa.Column(dtype, description=name, metadata={"unit": None})
                    for name, dtype in {
                        "id": str,
                        "value": float,
                        "geometry": Geometry,
                    }.items()
                }
            )
        )
        self.locations = self._construct()

    def _construct(self) -> Locations:
        return Locations.from_geodataframe(
            self.frame, collection="bench:points", unit_type="point", schema=self.schema
        )

    def time_construct(self, _rows: int) -> None:
        """Measure construction including production validation."""
        self._construct()

    def peakmem_construct(self, _rows: int) -> None:
        """Measure total process peak during construction."""
        self._construct()

    def time_export(self, _rows: int) -> None:
        """Measure dataframe export."""
        self.locations.to_geodataframe()

    def peakmem_export(self, _rows: int) -> None:
        """Measure total process peak during export."""
        self.locations.to_geodataframe()


class CodecSuite:
    """Encode and decode a scalar table."""

    params: ClassVar[list[int]] = [10_000, 100_000]
    param_names: ClassVar[list[str]] = ["rows"]

    def setup(self, rows: int) -> None:
        """Prepare snapshots and bytes outside timing."""
        frame = pd.DataFrame(
            {
                "id": [str(i) for i in range(rows)],
                "integer": range(rows),
                "value": [float(i) for i in range(rows)],
                "flag": [True] * rows,
            }
        )
        schema = pa.DataFrameSchema(
            {name: pa.Column(series.dtype) for name, series in frame.items()}
        )
        self.snapshot = Snapshot.from_frame(frame, schema)
        self.encoded = ipc_codec.encode(self.snapshot)

    def time_encode(self, _rows: int) -> None:
        """Measure encoding with cached fingerprints."""
        ipc_codec.encode(self.snapshot)

    def peakmem_encode(self, _rows: int) -> None:
        """Measure total process peak during encoding."""
        ipc_codec.encode(self.snapshot)

    def time_decode(self, _rows: int) -> None:
        """Measure decoding including production validation."""
        ipc_codec.decode(self.encoded)

    def peakmem_decode(self, _rows: int) -> None:
        """Measure total process peak during decoding."""
        ipc_codec.decode(self.encoded)
