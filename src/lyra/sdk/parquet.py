"""Local Parquet sources with an execution-owned spatial DuckDB backend."""

from __future__ import annotations

import json
import re
from functools import partial
from glob import has_magic
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pyarrow as pa
import pyarrow.parquet as pq
import pyproj
import shapely
from ibis import udf
from pydantic import Field, field_validator

from lyra.contracts.resources import IOConfig
from lyra.data.relational import geometry_wkt
from lyra.sdk.managed_provider import (
    ManagedLocationProvider,
    StagedCatalog,
    decode_records,
    staging_tables,
)
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import ProviderQuery, compile_query_schema

if TYPE_CHECKING:
    from collections.abc import Mapping

    import geopandas as gpd
    import ibis.expr.types as ir
    from ibis.backends.duckdb import Backend

    from lyra.contracts.interventions import AcceptedIntervention


@udf.scalar.builtin(name="ST_AsWKB")
def _as_wkb(geometry: dt.GeoSpatial) -> bytes:
    """Declare DuckDB's native WKB encoder with an explicit binary result type.

    Ibis 12 does not compile GeoAsBinary for DuckDB. This built-in declaration
    emits ST_AsWKB without registering a function or executing Python code.

    Raises:
        NotImplementedError: The declaration has no Python implementation.
    """
    raise NotImplementedError


def _check_geometry_metadata(source: Path, query: ProviderQuery) -> None:
    """Inspect only the file footer; record reads belong to the backend.

    Raises:
        TypeError: If the metadata does not contain column definitions.
        ValueError: If geometry storage, encoding, or CRS is incompatible.
    """
    native = pq.read_schema(source)
    if "geometry" not in native.names or not (
        pa.types.is_binary(native.field("geometry").type)
        or pa.types.is_large_binary(native.field("geometry").type)
    ):
        message = "Parquet geometry must use binary WKB storage"
        raise ValueError(message)
    encoded = (native.metadata or {}).get(b"geo")
    if encoded is None:
        return
    metadata = json.loads(encoded)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("columns"), dict):
        message = "Malformed GeoParquet metadata"
        raise TypeError(message)
    columns = metadata["columns"]
    geometry = columns.get("geometry")
    if (
        not isinstance(metadata.get("version"), str)
        or not metadata["version"]
        or not isinstance(metadata.get("primary_column"), str)
        or metadata["primary_column"] not in columns
        or not isinstance(geometry, dict)
    ):
        message = "GeoParquet metadata must describe the registered geometry column"
        raise ValueError(message)
    if geometry.get("encoding") != "WKB":
        message = "GeoParquet geometry must use WKB encoding"
        raise ValueError(message)
    if geometry.get("edges", "planar") != "planar":
        message = "GeoParquet geometry must use planar edges"
        raise ValueError(message)
    geometry_types = geometry.get("geometry_types", [])
    supported = {
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
        "GeometryCollection",
    }
    supported |= {f"{name} Z" for name in supported}
    if not isinstance(geometry_types, list) or any(
        not isinstance(name, str) or name not in supported for name in geometry_types
    ):
        message = "GeoParquet metadata declares unsupported geometry types"
        raise ValueError(message)
    crs = geometry.get("crs", "OGC:CRS84")
    # GeoParquet always stores x/y coordinates, irrespective of CRS axis order.
    if crs is None or not pyproj.CRS(crs).equals(
        pyproj.CRS(query.crs), ignore_axis_order=True
    ):
        message = "GeoParquet geometry CRS conflicts with the declared CRS"
        raise ValueError(message)


def _prepare_relation(relation: ir.Table, query: ProviderQuery) -> ir.Table:
    """Bind compatible source columns to the complete declared logical schema.

    Returns:
        A lazy relation containing the typed registered columns.

    Raises:
        ValueError: If source columns are absent or have incompatible types.
    """
    actual = relation.schema()
    for name, dtype in query.schema.items():
        source = actual.get(name)
        if isinstance(dtype, dt.GeoSpatial):
            compatible = isinstance(source, dt.Binary) or (
                isinstance(source, dt.GeoSpatial)
                and source.geotype == "geometry"
                and source.srid in {None, 0, dtype.srid}
            )
        else:
            compatible = source is not None and (
                source.copy(nullable=True) == dtype.copy(nullable=True)
                or (source.is_integer() and dtype.is_integer())
                or (source.is_floating() and dtype.is_floating())
            )
        if not compatible:
            message = (
                f"Incompatible Parquet column {name!r}: {source}, expected {dtype}"
            )
            raise ValueError(message)
    # DuckDB's Ibis compiler translates binary -> geometry to ST_GeomFromWKB.
    # Casting native geometry only attaches the validated logical CRS/type.
    return relation.select(
        *(relation[name].cast(dtype).name(name) for name, dtype in query.schema.items())
    )


class ParquetConfig(IOConfig):
    """Identify one local file, relative to the executing working directory."""

    path: str = Field(strict=True, min_length=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Reject source forms that could expand beyond one local file.

        Returns:
            The unchanged local path, without inspecting the filesystem.

        Raises:
            ValueError: If the source is blank, a pattern, or a URL.
        """
        if (
            not value.strip()
            or "\x00" in value
            or has_magic(value)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
            or value.startswith(("//", "\\\\"))
        ):
            message = "Parquet path must identify one local file without glob patterns"
            raise ValueError(message)
        return value


class ParquetLocationProvider(ManagedLocationProvider[ParquetConfig]):
    """Own a local file relation and its native DuckDB backend for one execution.

    File checks and spatial extension initialization occur only during setup.
    Authors declare the record schema; managed acquisition supplies resolution.
    """

    config_model = ParquetConfig

    def __init__(self, config: ParquetConfig, secrets: Mapping[str, str]) -> None:
        """Retain configuration without file access or backend imports."""
        super().__init__(config, secrets)
        self._backend: Backend | None = None
        self._relation: ir.Table | None = None
        self._staging = StagedCatalog()

    def setup(self) -> None:
        """Open one validated source and retain ownership before connecting.

        Missing or unreadable files propagate their filesystem errors.

        Raises:
            RuntimeError: If this provider already owns a backend.
            ValueError: If the resolved source is not a regular local file.
        """
        if self._backend is not None:
            message = "Parquet provider already owns a backend"
            raise RuntimeError(message)
        source = Path(self.config.path).resolve(strict=True)
        # Revalidate the resolved name: symlink targets must not turn into a
        # backend glob or URL either.
        ParquetConfig.validate_path(str(source))
        if not source.is_file():
            message = "Parquet path must identify a regular file"
            raise ValueError(message)
        with source.open("rb"):
            pass
        query = compile_query_schema(ProviderSchema(self.schema).schema)
        _check_geometry_metadata(source, query)
        backend_type = cast(
            "type[Backend]", import_module("ibis.backends.duckdb").Backend
        )
        # Retain the native object before reconnect: extension setup can fail
        # after the embedded connection has been assigned.
        self._backend = backend_type(extensions=["spatial"])
        self._backend.reconnect()
        # read_parquet creates a DuckDB temporary view. Keep the physical
        # namespace explicit when composing it with the request's staged rows.
        file_view = self._backend.read_parquet(str(source)).op()
        qualified = file_view.copy(
            namespace=ops.Namespace(catalog="temp", database="main")
        )
        self._relation = _prepare_relation(qualified.to_expr(), query)

    def _base_relation(self) -> ir.Table:
        """Return the shared file relation for managed acquisition.

        Raises:
            RuntimeError: If setup has not completed or cleanup has begun.
        """
        if self._relation is None:
            message = "Parquet provider source is not initialized"
            raise RuntimeError(message)
        return self._relation

    def close(self) -> None:
        """Attempt cleanup once, including after partial backend setup failure."""
        backend, self._backend = self._backend, None
        self._relation = None
        try:
            self._staging.close()
        finally:
            if backend is not None and hasattr(backend, "con"):
                backend.disconnect()

    def _materialize(
        self, relation: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        if self._backend is None:
            message = "Parquet provider source is not initialized"
            raise RuntimeError(message)
        # Use explicit binary geometry for Arrow, including zero-row results.
        # DuckDB's Arrow batches mark fields nullable, even for non-null source
        # columns. Match that transport schema; producer validation still
        # enforces the registered nullability after decoding.
        # Consume every batch and release its cursor before provider cleanup.
        interchange = relation.mutate(geometry=_as_wkb(relation.geometry))
        interchange = interchange.cast(
            {
                name: dtype.copy(nullable=True)
                for name, dtype in interchange.schema().items()
            }
        )
        with self._backend.to_pyarrow_batches(interchange, limit=None) as batches:
            data = batches.read_all()
        return decode_records(data, schema)

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Filter exact IDs in DuckDB and decode their complete registered records.

        Returns:
            Native records for validation at the ordinary producer boundary.
        """
        schema = ProviderSchema(self.schema)
        if not ids:
            return schema.empty_frame()
        relation = self._base_relation()
        return self._materialize(
            relation.filter(relation.location_id.isin(ids)), schema
        )

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return base membership using only the requested ID column.

        Raises:
            RuntimeError: If the source has not been initialized.
        """
        if not ids:
            return set()
        relation = self._base_relation()
        if self._backend is None:
            message = "Parquet provider source is not initialized"
            raise RuntimeError(message)
        selected = relation.select("location_id")
        selected = selected.filter(selected.location_id.isin(ids))
        # DuckDB Arrow fields are nullable regardless of source declarations.
        selected = selected.cast({"location_id": "string"})
        with self._backend.to_pyarrow_batches(selected, limit=None) as batches:
            return set(batches.read_all()["location_id"].to_pylist())

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Bind a validated filter to the file and decode all matching records.

        Returns:
            Native records for the shared candidate validation boundary.
        """
        return self._select_from(self._base_relation(), expression, schema)

    def _register_rows(self, name: str, data: pa.Table) -> ir.Table:
        if self._backend is None:
            message = "Parquet provider source is not initialized"
            raise RuntimeError(message)
        self._backend.con.register(name, data)
        self._staging.drops.append(partial(self._backend.con.unregister, name))
        return ibis.table(
            ibis.schema(data.schema), name=name, catalog="temp", database="main"
        )

    def select_effective(
        self,
        expression: ir.Table,
        schema: ProviderSchema,
        intervention: AcceptedIntervention,
    ) -> gpd.GeoDataFrame:
        """Return matching records, preparing the effective catalog on first use."""
        if self._staging.relation is None:
            base = self._base_relation()
            query = compile_query_schema(schema.schema)
            additions, removals = staging_tables(intervention, schema, query)
            token = uuid4().hex
            added = self._register_rows(f"lyra_added_{token}", additions)
            removed = self._register_rows(f"lyra_removed_{token}", removals)
            remaining = base.filter(base.location_id.notin(removed.location_id))
            self._staging.relation = remaining.union(
                _prepare_relation(added, query), distinct=False
            )
        return self._select_from(self._staging.relation, expression, schema)

    def _select_from(
        self, relation: ir.Table, expression: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        root = expression.op()
        while isinstance(root, ops.Filter):
            root = root.parent
        if not isinstance(root, ops.UnboundTable):
            message = "Parquet selection requires a validated symbolic filter"
            raise TypeError(message)
        expected_srid = pyproj.CRS(compile_query_schema(schema.schema).crs).to_epsg()
        replacements: dict[ops.Node, ops.Node] = {}
        for literal in expression.op().find(ops.Literal):
            if not isinstance(literal.dtype, dt.GeoSpatial):
                continue
            # Check both metadata sources before dropping the Ibis SRID.
            # DuckDB's second ST_GeomFromText argument is a Boolean, not an SRID.
            for srid in (literal.dtype.srid, int(shapely.get_srid(literal.value))):
                if srid not in {None, 0, expected_srid}:
                    message = (
                        f"Selector geometry SRID {srid} does not match target SRID "
                        f"{expected_srid}; reproject source geometry first"
                    )
                    raise ValueError(message)
            # A string-to-geometry cast emits the one-argument constructor
            # while allowing Lyra to control the coordinate precision.
            replacements[literal] = (
                ibis.literal(geometry_wkt(literal.value)).cast(literal.dtype).op()
            )
        replacements[root] = relation.op()
        # Recursive immutable replacement preserves fields on chained filters,
        # every predicate, and the author's original expression and coordinates.
        bound = expression.op().replace(replacements).to_expr()
        return self._materialize(bound, schema)
