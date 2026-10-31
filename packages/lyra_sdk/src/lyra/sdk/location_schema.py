"""Service-free Pandera contracts for resolved location values."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, cast

import geopandas as gpd
import pandas as pd
import pyarrow.compute as pc
import pyproj
from lyra.sdk.location_frames import validate_frame
from lyra.sdk.models.location import Locations, location_snapshot, validated_locations
from lyra.sdk.models.location_payload import LocationPayload
from lyra.sdk.models.output import LocationOutputMetadata
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.results import validate_registered_result
from lyra.sdk.scalars import normalize_property
from pandera.engines import pandas_engine

if TYPE_CHECKING:
    import pandera.pandas as pa
    from lyra.sdk.location_snapshot import LocationSnapshot


class LocationOutputSchema:
    """Compile a location declaration without acquiring resources.

    Columns require the descriptions and unit metadata used by metric outputs.
    ID and geometry names are explicit; all remaining columns are properties.
    Producer frames use native dtypes and declared coercion before construction.
    Snapshot revalidation reconstructs storage dtypes from JSON scalars. Both
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
    ) -> Locations:
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
        return Locations.from_geodataframe(
            frame, collection=collection, unit_type=unit_type, id_column=self.id_column
        )

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
        if set(frame.columns) != set(self.schema.columns):
            msg = "Location frame must contain exactly the declared columns"
            raise ValueError(msg)
        identifiers = frame[self.id_column]
        if any(type(value) is not str or not value for value in identifiers):
            msg = "Feature id must be a nonempty string"
            raise ValueError(msg)
        if not identifiers.is_unique:
            msg = "IDs must be unique within the scope"
            raise ValueError(msg)
        for name in self.schema.columns.keys() - {self.id_column, self.geometry_column}:
            if frame[name].dtype == object and any(
                value is pd.NA for value in frame[name]
            ):
                msg = f"Missing required property {name!r}"
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

    def validate(self, value: Locations, *, lazy: bool = False) -> Locations:
        """Return independent validated locations, including declared coercion.

        Empty values receive every declared column. Populated values must supply
        exactly the declared properties on every feature, including nullable ones.
        The frame index is a fresh RangeIndex; original indexes are not retained.

        Returns:
            Locations preserving scope, row order, and CRS.

        Raises:
            TypeError: If the result is not Locations.
            ValueError: If property presence or shared invariants are invalid.
        """
        if not isinstance(value, Locations):
            msg = "Location output must be Locations"
            raise TypeError(msg)
        value = validated_locations(value)
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
        _validate_property_presence(location_snapshot(value), properties)
        frame = value.to_geodataframe(
            id_column=self.id_column, geometry_column=self.geometry_column
        )
        for name, column in self.schema.columns.items():
            if name == self.geometry_column:
                continue
            source = frame[name] if name in frame else pd.Series([], dtype=object)
            frame[name] = _reconstruct(
                source, column, coerce=self.schema.coerce or column.coerce
            )
        frame = frame[list(self.schema.columns)]
        validated = validate_registered_result(
            "locations", {"locations": self.schema}, frame, lazy=lazy
        )["locations"]
        result = Locations.from_geodataframe(
            cast("gpd.GeoDataFrame", validated),
            collection=value.collection,
            unit_type=value.unit_type,
            id_column=self.id_column,
        )
        if pyproj.CRS(result.crs.properties.name) != pyproj.CRS(
            value.crs.properties.name
        ):
            msg = "Location schema processing must preserve CRS"
            raise ValueError(msg)
        if result.crs == value.crs:
            return result
        # Conversion may canonicalize the CRS spelling, but never its identity.
        return Locations.model_validate(result.model_dump() | {"crs": value.crs})


def _validate_property_presence(snapshot: LocationSnapshot, declared: set[str]) -> None:
    """Check property tags on owned columns without exporting feature contents.

    Raises:
        ValueError: If the first offending row has missing or extra properties.
    """
    table = snapshot.properties
    if not table.num_rows:
        return
    keys = json.loads(table.schema.metadata[b"location"])["keys"]
    first = 0 if declared - set(keys) else table.num_rows
    for index, key in enumerate(keys, 1):
        if first == 0:
            break
        tags = table.column(index).flatten()[0]
        mismatch = (
            pc.index(tags, 0)
            if key in declared
            else pc.index(pc.call_function("not_equal", [tags, 0]), True)
        ).as_py()
        if mismatch >= 0:
            first = min(first, mismatch)
    if first == table.num_rows:
        return
    present = {
        key
        for index, key in enumerate(keys, 1)
        if table.column(index).flatten()[0][first].as_py() != 0
    }
    identifier = table.column("id")[first].as_py()
    msg = (
        f"Feature {identifier!r}: missing properties {sorted(declared - present)!r}; "
        f"unexpected properties {sorted(present - declared)!r}"
    )
    raise ValueError(msg)


def _reconstruct(source: pd.Series, column: pa.Column, *, coerce: bool) -> pd.Series:
    """Restore declared storage without implicitly changing scalar meaning.

    Returns:
        A series with the declared dtype.

    Raises:
        ValueError: If reconstruction fails or needs undeclared scalar coercion.
    """
    dtype = column.dtype
    try:
        decoded = _decode(source, column, coerce=coerce)
        restored = dtype.coerce(decoded)
    except (TypeError, ValueError, OverflowError) as exc:
        msg = f"Cannot reconstruct location column {column.name!r}: {exc}"
        raise ValueError(msg) from exc
    if not coerce:
        for original, converted in zip(source, restored, strict=True):
            before = normalize_property(original, str(column.name))
            after = normalize_property(converted, str(column.name))
            if before != after or (isinstance(before, bool) != isinstance(after, bool)):
                msg = f"Location column {column.name!r} requires explicit coercion"
                raise ValueError(msg)
    return restored


def _decode(source: pd.Series, column: pa.Column, *, coerce: bool) -> pd.Series:
    """Return temporal scalars and categories reconstructed from JSON storage."""
    dtype = column.dtype
    categories = getattr(dtype, "categories", None)
    if categories is not None:
        lookup = {
            (type(normalized), normalized): category
            for category in categories
            for normalized in [normalize_property(category, str(column.name))]
        }
        return pd.Series(
            [lookup.get((type(value), value), value) for value in source],
            index=source.index,
            dtype=object,
        )
    if not coerce and isinstance(dtype, pandas_engine.Date | pandas_engine.DateTime):
        parse = (
            date.fromisoformat
            if isinstance(dtype, pandas_engine.Date)
            else pd.Timestamp
        )
        return pd.Series(
            [parse(value) if isinstance(value, str) else value for value in source],
            index=source.index,
            dtype=object,
        )
    return source
