"""Internal native schema boundary for provider registration and acquisition."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines import numpy_engine, pandas_engine

from lyra.sdk.location_schema import LocationOutputSchema

if TYPE_CHECKING:
    from lyra.sdk.output_schema import OutputSchema


class ProviderSchema:
    """Snapshot a strict provider declaration without acquiring resources.

    Provider declarations supply the scope, opaque ID column, geometry CRS,
    and predicates for each fresh batch of records.
    """

    def __init__(self, declaration: OutputSchema) -> None:
        """Compile fixed provider columns and their existing output metadata.

        Raises:
            TypeError: If the declaration is not an executable Pandera schema.
            ValueError: If strictness or structural declarations are invalid.
        """
        if isinstance(declaration, type) and issubclass(declaration, pa.DataFrameModel):
            declaration = declaration.to_schema()
        if not isinstance(declaration, pa.DataFrameSchema):
            msg = "Providers require a Pandera schema"
            raise TypeError(msg)
        if declaration.strict is not True:
            msg = "Provider schemas require strict=True"
            raise ValueError(msg)
        if {"source_location_id", "row_key"} & declaration.columns.keys():
            msg = "Provider attributes cannot use reserved structural column names"
            raise ValueError(msg)
        compiled = LocationOutputSchema(declaration, id_column="location_id")
        identifier = compiled.schema.columns["location_id"]
        if (
            not isinstance(identifier.dtype, numpy_engine.String | pandas_engine.STRING)
            or identifier.nullable
            or not identifier.unique
            or not identifier.checks
        ):
            msg = (
                "location_id must declare unique, non-null strings "
                "with a nonempty check"
            )
            raise ValueError(msg)
        geometry = compiled.schema.columns["geometry"]
        if (
            geometry.nullable
            or getattr(geometry.dtype, "crs", None) is None
            or not geometry.checks
        ):
            msg = "geometry must declare non-null values, an expected CRS, and checks"
            raise ValueError(msg)
        self._compiled = compiled
        self.storage = compiled.storage
        self.schema = compiled.schema
        self.metadata = compiled.metadata.output

    def validate(
        self, frame: gpd.GeoDataFrame, *, lazy: bool = False
    ) -> gpd.GeoDataFrame:
        """Return a detached native frame satisfying the full provider schema.

        Types, active geometry and CRS survive empty and all-null columns.
        Authors must rename columns and reproject before returning their frames.
        """
        if not isinstance(frame, gpd.GeoDataFrame) or not frame.columns.is_unique:
            return self._compiled.prepare_frame(frame, lazy=lazy)
        compiled = self._compiled
        for name, column in self.schema.columns.items():
            if name not in frame:
                continue
            dtype = frame[name].dtype
            string_storage = (
                isinstance(dtype, pd.StringDtype)
                and isinstance(column.dtype, pandas_engine.STRING)
                and dtype.storage != column.dtype.storage
            )
            nullable_boolean = (
                str(column.dtype) == "bool"
                and column.nullable
                and isinstance(dtype, pd.BooleanDtype)
            )
            if string_storage or nullable_boolean:
                if compiled is self._compiled:
                    compiled = copy(compiled)
                    compiled.schema = copy(self.schema)
                    compiled.schema.columns = self.schema.columns.copy()
                compatible = copy(column)
                compatible.dtype = dtype
                compiled.schema.columns[name] = compatible
        return compiled.prepare_frame(frame, lazy=lazy)

    def empty_frame(self) -> gpd.GeoDataFrame:
        """Return declared empty columns and CRS without acquiring a provider."""
        crs = self.schema.columns["geometry"].dtype.crs
        return self._compiled.empty_frame(str(crs))
