"""Read a declared PostgreSQL relation through a borrowed native backend."""

from functools import partial
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
    StagedCatalog,
    decode_records,
    staging_tables,
)
from lyra.sdk.postgres import PostgresIO
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from lyra.sdk.resources import resource_dependencies

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pyarrow as pa

    from lyra.contracts.interventions import AcceptedIntervention
    from lyra.contracts.resources import IOConfig


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

    def __init__(self, config: "IOConfig", secrets: "Mapping[str, str]") -> None:
        """Store the provider configuration for this execution."""
        super().__init__(config, secrets)
        self._staging = StagedCatalog()

    def close(self) -> None:
        """Drop temporary tables for this catalog before the connection closes."""
        self._staging.close()

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

    def _base_relation(self, schema: ProviderSchema) -> ir.Table:
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

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Read complete registered records for the requested IDs.

        Returns:
            Native records for validation at the ordinary producer boundary.
        """
        schema = ProviderSchema(self.schema)
        if not ids:
            return schema.empty_frame()
        relation = self._base_relation(schema)
        return self._materialize(
            relation.filter(relation.location_id.isin(ids)), schema
        )

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return base membership through the borrowed backend's ID-only read."""
        if not ids:
            return set()
        relation = self._base_relation(ProviderSchema(self.schema))
        selected = relation.select("location_id")
        selected = selected.filter(selected.location_id.isin(ids))
        with self.database.backend.to_pyarrow_batches(selected, limit=None) as batches:
            return set(batches.read_all()["location_id"].to_pylist())

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Run a validated selector against the base relation.

        The selector must start from a symbolic target table.

        Returns:
            Complete records that match the selector.
        """
        return self._select_from(self._base_relation(schema), expression, schema)

    def _stage_table(self, name: str, data: "pa.Table") -> ir.Table:
        backend = self.database.backend
        table = backend.create_table(
            name, schema=ibis.schema(data.schema), database="pg_temp", temp=True
        )
        # CREATE has committed. Register cleanup before COPY can fail, and
        # never claim a table whose CREATE failed (including a name collision).
        self._staging.drops.append(partial(self._drop_table, name))
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

    def _drop_table(self, name: str) -> None:
        # Ibis 12's drop_table emits a bare DROP TABLE with the locked SQLGlot.
        with self.database.backend.begin() as cursor:
            cursor.execute(f'DROP TABLE pg_temp."{name}"')

    def select_effective(
        self,
        expression: ir.Table,
        schema: ProviderSchema,
        intervention: "AcceptedIntervention",
    ) -> gpd.GeoDataFrame:
        """Return matching records after applying additions and removals."""
        if self._staging.relation is None:
            base = self._base_relation(schema)
            query = compile_query_schema(schema.schema)
            additions, removals = staging_tables(intervention, schema, query)
            token = uuid4().hex
            added = self._stage_table(f"lyra_added_{token}", additions)
            removed = self._stage_table(f"lyra_removed_{token}", removals)
            geometry = cast("dt.GeoSpatial", query.schema["geometry"])
            added = added.mutate(geometry=_from_wkb(added.geometry, geometry.srid))
            remaining = base.filter(base.location_id.notin(removed.location_id))
            # The source may expose narrower scalars or a concrete geometry
            # subtype. Cast to common union types without constraining geometry
            # dimensions or changing coordinates/SRIDs on the base branch.
            remaining = remaining.mutate(
                **{
                    name: ops.Cast(remaining[name], to=dtype).to_expr()
                    for name, dtype in added.schema().items()
                    if remaining.schema()[name] != dtype
                }
            )
            self._staging.relation = remaining.union(added, distinct=False)
        return self._select_from(self._staging.relation, expression, schema)

    def _select_from(
        self, relation: ir.Table, expression: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        root = expression.op()
        while isinstance(root, ops.Filter):
            root = root.parent
        if not isinstance(root, ops.UnboundTable):
            message = "PostGIS selection requires a validated symbolic filter"
            raise TypeError(message)
        # Recursive replacement also rebinds fields referencing intermediate
        # filters. Ibis nodes are immutable, so the author expression is retained.
        replacements: dict[ops.Node, ops.Node] = {root: relation.op()}
        for literal in expression.op().find(ops.Literal):
            if isinstance(literal.dtype, dt.GeoSpatial):
                replacements[literal] = _from_wkt(
                    geometry_wkt(literal.value),
                    literal.dtype.srid or int(shapely.get_srid(literal.value)),
                ).op()
        bound = expression.op().replace(replacements).to_expr()
        return self._materialize(bound, schema)
