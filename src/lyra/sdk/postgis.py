"""Read a declared PostgreSQL relation through a borrowed native backend."""

from typing import ClassVar

import geopandas as gpd
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from lyra.sdk.managed_provider import ManagedLocationProvider, decode_records
from lyra.sdk.postgres import PostgresIO
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import compile_query_schema
from lyra.sdk.resources import resource_dependencies


class PostGISLocationProvider(ManagedLocationProvider):
    """Declare one existing table, view, or materialized view and its database.

    Subclasses bind ``database: Annotated[PostgresIO, Resource("key")]``.
    The resource owns the connection; this provider only borrows it for reads.
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

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Bind a validated selector and materialize its complete matching records.

        Returns:
            Native records for the shared candidate validation boundary.

        Raises:
            TypeError: If the expression is not rooted in a symbolic relation.
        """
        root = expression.op()
        while isinstance(root, ops.Filter):
            root = root.parent
        if not isinstance(root, ops.UnboundTable):
            message = "PostGIS selection requires a validated symbolic filter"
            raise TypeError(message)
        relation = self._base_relation(schema)
        # Recursive replacement also rebinds fields referencing intermediate
        # filters. Ibis nodes are immutable, so the author expression is retained.
        # PostgreSQL supports SRID-bearing geometry literals without translation.
        bound = expression.op().replace({root: relation.op()}).to_expr()
        return self._materialize(bound, schema)
