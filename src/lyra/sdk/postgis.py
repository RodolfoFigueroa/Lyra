"""Read a declared PostgreSQL relation through a borrowed native backend."""

from typing import TYPE_CHECKING, ClassVar, cast
from uuid import uuid4

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import shapely

from lyra.data.relational import geometry_wkt
from lyra.sdk.managed_provider import (
    ManagedLocationProvider,
    decode_records,
)
from lyra.sdk.postgres import PostgresIO
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import ProviderQuery, compile_query_schema
from lyra.sdk.resources import resource_dependencies

if TYPE_CHECKING:
    import pyarrow as pa


@ibis.udf.scalar.builtin(name="ST_GeomFromWKB")
def _from_wkb(value: bytes, srid: dt.Int32) -> dt.GeoSpatial:
    """PostGIS constructor for staged binary geometry in the registered CRS.

    Raises:
        NotImplementedError: The database implements this function.
    """
    raise NotImplementedError


@ibis.udf.scalar.builtin(name="ST_GeomFromText")
def _from_wkt(value: str, srid: dt.Int32) -> dt.GeoSpatial:
    """PostGIS constructor for full-precision selector literals.

    Raises:
        NotImplementedError: The database implements this function.
    """
    raise NotImplementedError


class PostGISLocationProvider(ManagedLocationProvider):
    """Declare one existing table, view, or materialized view and its database.

    Bind `database: Annotated[PostgresIO, Resource("key")]` on your subclass.
    The database resource owns the connection. Grant its role `TEMPORARY` on
    the database if a selector queries a catalog with additions or removals.
    """

    database: PostgresIO
    db_schema: ClassVar[str]
    table: ClassVar[str]

    @classmethod
    def validate_declaration(cls) -> None:
        """Check source declarations without accessing configuration or services.

        Raises:
            ValueError: If the source or database requirement is invalid.
        """
        for name in ("db_schema", "table"):
            value = getattr(cls, name, None)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                message = f"PostGIS providers require a nonempty {name} identifier"
                raise ValueError(message)
        dependency = resource_dependencies(cls).get("database")
        if dependency is None or not issubclass(dependency[0], PostgresIO):
            message = "PostGIS database must declare an annotated PostgresIO resource"
            raise ValueError(message)

    def _base_relation(self, schema: ProviderSchema, /) -> ir.Table:
        relation = self.database.backend.table(self.table, database=self.db_schema)
        expected = compile_query_schema(schema.schema).schema
        actual = relation.schema()
        for name, dtype in expected.items():
            source = actual.get(name)
            if isinstance(dtype, dt.GeoSpatial):
                compatible = (
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
                    f"Incompatible PostGIS column {name!r}: {source}, expected {dtype}"
                )
                raise ValueError(message)
        return relation.select(*list(expected))

    def _materialize(
        self, relation: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        # Typed Arrow batches preserve nullable integers without pandas float
        # inference. EWKB retains SRIDs so conflicting metadata can be rejected.
        interchange = relation.mutate(geometry=relation.geometry.as_ewkb())
        with self.database.backend.to_pyarrow_batches(
            interchange, limit=None
        ) as batches:
            data = batches.read_all()
        return decode_records(data, schema)

    def _read_ids(self, selected: ir.Table) -> set[str]:
        with self.database.backend.to_pyarrow_batches(selected, limit=None) as batches:
            return set(batches.read_all()["location_id"].to_pylist())

    def _stage_table(self, name: str, data: "pa.Table") -> ir.Table:
        backend = self.database.backend
        table = backend.create_table(
            name, schema=ibis.schema(data.schema), database="pg_temp", temp=True
        )
        if data.num_rows:
            # Only the generated table name enters SQL. The driver encodes
            # each typed cell, including BYTEA geometry and SQL NULLs.
            with (
                backend.begin() as cursor,
                cursor.copy(f'COPY pg_temp."{name}" FROM STDIN') as stream,
            ):
                for batch in data.to_batches(max_chunksize=8192):
                    for row in zip(
                        *(column.to_pylist() for column in batch.columns), strict=True
                    ):
                        stream.write_row(row)
        return table

    def _stage(
        self, query: ProviderQuery, additions: "pa.Table", removals: "pa.Table"
    ) -> tuple[ir.Table, ir.Table]:
        token = uuid4().hex
        added = self._stage_table(f"lyra_added_{token}", additions)
        removed = self._stage_table(f"lyra_removed_{token}", removals)
        geometry = cast("dt.GeoSpatial", query.schema["geometry"])
        added = added.mutate(geometry=_from_wkb(added.geometry, geometry.srid))
        return added, removed

    @staticmethod
    def _literal_replacements(
        expression: ir.Table, _schema: ProviderSchema
    ) -> dict[ops.Node, ops.Node]:
        replacements: dict[ops.Node, ops.Node] = {}
        for literal in expression.op().find(ops.Literal):
            if isinstance(literal.dtype, dt.GeoSpatial):
                replacements[literal] = _from_wkt(
                    geometry_wkt(literal.value),
                    literal.dtype.srid or int(shapely.get_srid(literal.value)),
                ).op()
        return replacements
