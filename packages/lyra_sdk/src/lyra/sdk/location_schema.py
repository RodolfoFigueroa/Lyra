"""Service-free Pandera contracts for resolved location values."""

from __future__ import annotations

from typing import cast

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
from lyra.sdk import arrow_conversion
from lyra.sdk.location_frames import validate_frame
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models import location
from lyra.sdk.models.location_payload import LocationPayload
from lyra.sdk.models.output import LocationOutputMetadata
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.results import validate_registered_result


class LocationOutputSchema:
    """Compile a location declaration without acquiring resources.

    Columns require the descriptions and unit metadata used by metric outputs.
    ID and geometry names are explicit; all remaining columns are properties.
    Producer frames use native dtypes and declared coercion before construction.
    Snapshot revalidation exports native declared dtypes. Both
    paths apply Pandera checks and shared location invariants.
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
        self.schema = schema
        self.metadata = LocationOutputMetadata(id_column=id_column, output=output)
        self.id_column = id_column
        self.geometry_column = geometry_column

    def build(
        self,
        frame: gpd.GeoDataFrame,
        *,
        collection: str,
        unit_type: str,
        lazy: bool = False,
    ) -> location.Locations:
        """Validate a producer frame and own its contents exactly once.

        Returns:
            A schema-validated immutable location snapshot.
        """
        validate_frame(frame, self.id_column)
        LocationPayload(
            collection=collection,
            unit_type=unit_type,
            crs={"type": "name", "properties": {"name": str(frame.crs)}},
            features=[],
        )
        frame = self.prepare_frame(frame, lazy=lazy)
        return self._from_prepared_frame(
            frame, collection=collection, unit_type=unit_type
        )

    def _from_prepared_frame(
        self, frame: gpd.GeoDataFrame, *, collection: str, unit_type: str
    ) -> location.Locations:
        """Own a frame already validated by this contract.

        Returns:
            The owned runtime value.
        """
        snapshot = LocationSnapshot.from_locations(
            frame,
            self.schema,
            collection=collection,
            unit_type=unit_type,
            id_column=self.id_column,
        )
        return location.locations_from_snapshot(snapshot)

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
        if not frame.columns.is_unique or set(frame.columns) != set(
            self.schema.columns
        ):
            msg = "Location frame must contain exactly the declared columns"
            raise ValueError(msg)
        identifiers = frame[self.id_column]
        if any(type(value) is not str or not value for value in identifiers):
            msg = "Feature id must be a nonempty string"
            raise ValueError(msg)
        if not identifiers.is_unique:
            msg = "IDs must be unique within the scope"
            raise ValueError(msg)

        for name, column in self.schema.columns.items():
            if not column.nullable and frame[name].isna().any():
                msg = f"Column {name!r} is not nullable"
                raise ValueError(msg)

    def empty_frame(self, crs: str) -> gpd.GeoDataFrame:
        """Return a typed empty frame without acquiring resources."""
        data = {}
        for name, column in self.schema.columns.items():
            if name != self.geometry_column:
                data[name] = column.dtype.coerce(pd.Series([], dtype=object))
        return gpd.GeoDataFrame(
            data | {self.geometry_column: gpd.GeoSeries([], crs=crs)},
            geometry=self.geometry_column,
        )[list(self.schema.columns)]

    def validate(
        self, value: location.Locations, *, lazy: bool = False
    ) -> location.Locations:
        """Validate detached frames, reusing unchanged owned locations.

        Every value must contain exactly the declared typed columns. Checks run
        on a detached frame with a fresh RangeIndex. Unchanged storage and cached
        fingerprints are reused only after the current checks succeed.

        Returns:
            Locations preserving scope, row order, and CRS.

        Raises:
            TypeError: If the result is not Locations.
            ValueError: If property presence or shared invariants are invalid.
        """
        if not isinstance(value, location.Locations):
            msg = "Location output must be Locations"
            raise TypeError(msg)
        value = location.validated_locations(value)
        declared_crs = getattr(
            self.schema.columns[self.geometry_column].dtype, "crs", None
        )
        if declared_crs is not None and pyproj.CRS(declared_crs) != pyproj.CRS(
            value.crs.properties.name
        ):
            msg = "Location geometry CRS must match the schema; reproject explicitly"
            raise ValueError(msg)
        properties = self.schema.columns.keys() - {
            self.id_column,
            self.geometry_column,
        }
        if set(value.property_names) != properties:
            msg = "Location properties must match the declared columns"
            raise ValueError(msg)
        frame = value.to_geodataframe(
            id_column=self.id_column, geometry_column=self.geometry_column
        )
        validated = self.prepare_frame(frame, lazy=lazy)
        snapshot = location.location_snapshot(value)
        # Checks always run on detached frames. Compare the current declaration
        # too: equal values alone do not establish an equal storage contract.
        unchanged = frame.equals(validated) and frame.geometry.to_wkb().equals(
            validated.geometry.to_wkb()
        )
        for name in frame.select_dtypes(include=[np.floating]).columns:
            unchanged = unchanged and np.array_equal(
                np.signbit(frame[name].to_numpy(dtype=float, na_value=np.nan)),
                np.signbit(validated[name].to_numpy(dtype=float, na_value=np.nan)),
            )
        if (
            unchanged
            and snapshot.scope.columns == tuple(self.schema.columns)
            and snapshot.scope.id_column == self.id_column
            and snapshot.scope.geometry_column == self.geometry_column
            and snapshot.table.schema.equals(
                arrow_conversion.arrow_schema(self.schema), check_metadata=True
            )
        ):
            return value
        if (
            snapshot.scope.columns == tuple(self.schema.columns)
            and snapshot.scope.id_column == self.id_column
            and snapshot.scope.geometry_column == self.geometry_column
            and frame.geometry.to_wkb().equals(validated.geometry.to_wkb())
        ):
            table = arrow_conversion.to_arrow(validated, self.schema)
            return location.locations_from_snapshot(
                snapshot.with_validated_table(table)
            )
        return self._from_prepared_frame(
            validated, collection=value.collection, unit_type=value.unit_type
        )
