"""Declaration-derived query and binary interchange types for managed providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import pyarrow as pa
import pyproj
from pandera.engines import geopandas_engine, numpy_engine, pandas_engine

from lyra.contracts.providers import ProviderQuerySchema, QueryColumn

if TYPE_CHECKING:
    import ibis.expr.types as ir
    import pandera.pandas as pandera


@dataclass(frozen=True)
class ProviderQuery:
    """Immutable query types captured independently of mutable provider frames."""

    schema: ibis.Schema
    interchange: pa.Schema
    crs: str

    @property
    def metadata(self) -> ProviderQuerySchema:
        """A fresh, JSON-safe description with no executable expressions."""
        return ProviderQuerySchema(
            columns=[
                QueryColumn(name=name, dtype=str(dtype), nullable=dtype.nullable)
                for name, dtype in self.schema.items()
            ],
            crs=self.crs,
        )

    def target_relation(self) -> ir.Table:
        """Create an unbound collection for one invocation, never a record copy.

        Validation must compare the supplied relation operation by object identity
        (`is`), not structural equality: an independently constructed table with
        the same name and schema is not the supplied target. The unique name also
        prevents ordinary Ibis structural rewrites from merging invocations.

        Returns:
            A fresh symbolic table with no backend or acquired records.
        """
        return ibis.table(self.schema, name=f"lyra_target_{uuid4().hex}")


def compile_query_schema(declaration: pandera.DataFrameSchema) -> ProviderQuery:
    """Translate supported executable column types without inspecting any values.

    Returns:
        Logical Ibis types and explicit Arrow types for WKB interchange.

    Raises:
        ValueError: If a declared type or CRS cannot be represented for queries.
    """
    scalar_types = {
        "int64": (dt.Int64, pa.int64()),
        "Int64": (dt.Int64, pa.int64()),
        "float64": (dt.Float64, pa.float64()),
        "Float64": (dt.Float64, pa.float64()),
        "bool": (dt.Boolean, pa.bool_()),
        "boolean": (dt.Boolean, pa.bool_()),
        "str": (dt.String, pa.string()),
        "string": (dt.String, pa.string()),
    }
    types = {}
    fields = []
    crs = str(declaration.columns["geometry"].dtype.crs)
    for name, column in declaration.columns.items():
        if isinstance(column.dtype, geopandas_engine.Geometry):
            srid = pyproj.CRS(crs).to_epsg()
            if srid is None:
                message = f"Query column {name!r} requires a CRS with an EPSG SRID"
                raise ValueError(message)
            dtype = dt.GeoSpatial(
                geotype="geometry", srid=srid, nullable=column.nullable
            )
            binary_type = pa.binary()
        elif isinstance(column.dtype, numpy_engine.String | pandas_engine.STRING):
            dtype = dt.String(nullable=column.nullable)
            binary_type = pa.string()
        else:
            pair = scalar_types.get(str(column.dtype))
            if pair is None:
                message = f"Unsupported query dtype for column {name!r}: {column.dtype}"
                raise ValueError(message)
            logical_type, binary_type = pair
            dtype = logical_type(nullable=column.nullable)
        types[name] = dtype
        fields.append(pa.field(name, binary_type, nullable=column.nullable))
    return ProviderQuery(ibis.schema(types), pa.schema(fields), crs)
