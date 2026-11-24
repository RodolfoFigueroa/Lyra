"""Service-free Pandera contracts for resolved location values."""

from __future__ import annotations

from typing import cast

import geopandas as gpd
import pyproj

from lyra.contracts.outputs import LocationOutputMetadata
from lyra.data.location_frames import validate_record_ids
from lyra.sdk import storage_schema
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.results import validate_registered_result


class LocationOutputSchema:
    """Compile a location declaration without acquiring resources.

    Columns require the descriptions and unit metadata used by metric outputs.
    ID and geometry names are explicit; all remaining columns are properties.
    Validation returns independent native frames with declared dtypes and
    positional indices, applying Pandera checks and shared location invariants.
    """

    def __init__(
        self,
        declaration: OutputSchema,
        *,
        id_column: str = "id",
        geometry_column: str = "geometry",
    ) -> None:
        """Snapshot a Pandera declaration and identify its structural columns.

        Raises:
            ValueError: If structural columns are absent, equal, or misidentified.
        """
        schema, output = compile_output(declaration)
        if (
            id_column == geometry_column
            or id_column not in schema.columns
            or output.geometry != geometry_column
        ):
            msg = "Declare distinct ID and geometry columns with the specified names"
            raise ValueError(msg)
        self.storage = storage_schema.describe(schema, metadata=output)
        self.schema = schema
        self.metadata = LocationOutputMetadata(id_column=id_column, output=output)
        self.id_column = id_column
        self.geometry_column = geometry_column

    def prepare_frame(
        self, frame: gpd.GeoDataFrame, *, lazy: bool = False
    ) -> gpd.GeoDataFrame:
        """Return an independent schema-validated working frame.

        Raises:
            TypeError: If the input is not a GeoDataFrame.
        """
        if not isinstance(frame, gpd.GeoDataFrame):
            msg = "Location records require a GeoDataFrame"
            raise TypeError(msg)
        validate_record_ids(
            frame,
            self.id_column,
            check_unique=not self.schema.columns[self.id_column].unique,
        )
        return cast(
            "gpd.GeoDataFrame",
            validate_registered_result(
                "locations", {"locations": self.schema}, frame, lazy=lazy
            )["locations"],
        )

    def empty_frame(self, crs: str) -> gpd.GeoDataFrame:
        """Return a typed empty frame without acquiring resources.

        Raises:
            ValueError: If the requested CRS differs from the declaration.
        """
        declared = self.schema.columns[self.geometry_column].dtype.crs
        if pyproj.CRS(crs) != pyproj.CRS(declared):
            msg = "Empty frame CRS must match the declaration"
            raise ValueError(msg)
        return cast("gpd.GeoDataFrame", storage_schema.empty_frame(self.schema))
