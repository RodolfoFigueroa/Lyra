"""Service-free Pandera contracts for resolved location values."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pyproj

from lyra.contracts.outputs import LocationOutputMetadata
from lyra.data.location_frames import validate_frame, validate_record_columns
from lyra.sdk import storage_schema
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.results import validate_registered_result

if TYPE_CHECKING:
    import geopandas as gpd


class LocationOutputSchema:
    """Compile a location declaration without acquiring resources.

    Columns require the descriptions and unit metadata used by metric outputs.
    ID and geometry names are explicit; all remaining columns are properties.
    Validation returns independent native frames with declared dtypes and
    coercion, applying Pandera checks and shared location invariants.
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
            ValueError: If structural metadata or final invariants are invalid.
        """
        validate_frame(frame, self.id_column)
        if frame.active_geometry_name != self.geometry_column:
            msg = "Active geometry must match the declared geometry column"
            raise ValueError(msg)
        if frame.crs is None:
            msg = "Known CRS required"
            raise ValueError(msg)
        original_crs = frame.crs
        declared_crs = getattr(
            self.schema.columns[self.geometry_column].dtype, "crs", None
        )
        if declared_crs is not None and pyproj.CRS(declared_crs) != original_crs:
            msg = "Location geometry CRS must match the schema; reproject explicitly"
            raise ValueError(msg)
        frame = frame.copy(deep=True).reset_index(drop=True)
        self._check_frame(frame)
        validated = cast(
            "gpd.GeoDataFrame",
            validate_registered_result(
                "locations", {"locations": self.schema}, frame, lazy=lazy
            )["locations"],
        )
        validate_frame(validated, self.id_column)
        if (
            validated.active_geometry_name != self.geometry_column
            or validated.crs != original_crs
        ):
            msg = "Location schema processing must preserve geometry column and CRS"
            raise ValueError(msg)
        self._check_frame(validated)
        return validated.reset_index(drop=True)

    def _check_frame(self, frame: gpd.GeoDataFrame) -> None:
        validate_record_columns(frame, self.id_column, self.schema.columns)
        for name, column in self.schema.columns.items():
            if not column.nullable and frame[name].isna().any():
                msg = f"Column {name!r} is not nullable"
                raise ValueError(msg)

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
