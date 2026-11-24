"""Internal class boundary for Lyra's managed provider implementations."""

from typing import TYPE_CHECKING

import geopandas as gpd
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyproj
import shapely

from lyra.data.intervention_records import accepted_additions
from lyra.sdk.locations import LocationProvider
from lyra.sdk.provider_schema import ProviderSchema
from lyra.sdk.query_schema import ProviderQuery, compile_query_schema
from lyra.sdk.resources import ConfigT

if TYPE_CHECKING:
    from lyra.contracts.interventions import AcceptedIntervention


class ManagedLocationProvider(LocationProvider[ConfigT]):
    """Identify built-in query adapters by inheritance, without probing resources.

    Managed implementations own both exact-ID and query acquisition.
    """

    _effective_relation: ir.Table | None = None

    def _base_relation(self, schema: ProviderSchema, /) -> ir.Table:
        raise NotImplementedError

    def _materialize(
        self, relation: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        raise NotImplementedError

    def _read_ids(self, selected: ir.Table) -> set[str]:
        raise NotImplementedError

    def _stage(
        self, query: ProviderQuery, additions: pa.Table, removals: pa.Table
    ) -> tuple[ir.Table, ir.Table]:
        raise NotImplementedError

    @staticmethod
    def _literal_replacements(
        expression: ir.Table, schema: ProviderSchema, /
    ) -> dict[ops.Node, ops.Node]:
        raise NotImplementedError

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        """Return complete base records for the requested IDs."""
        schema = ProviderSchema(self.schema)
        if not ids:
            return schema.empty_frame()
        base = self._base_relation(schema)
        return self._materialize(base.filter(base.location_id.isin(ids)), schema)

    def existing_ids(self, ids: list[str]) -> set[str]:
        """Return base membership using only the ID column."""
        if not ids:
            return set()
        base = self._base_relation(ProviderSchema(self.schema))
        return self._read_ids(
            base.filter(base.location_id.isin(ids)).select("location_id")
        )

    def select(self, expression: ir.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
        """Return matching base records for a validated selector."""
        return self._select_from(self._base_relation(schema), expression, schema)

    def select_effective(
        self,
        expression: ir.Table,
        schema: ProviderSchema,
        intervention: "AcceptedIntervention",
    ) -> gpd.GeoDataFrame:
        """Return matching effective records, staging changes on first use."""
        if self._effective_relation is None:
            base = self._base_relation(schema)
            query = compile_query_schema(schema.schema)
            frame = accepted_additions(intervention)
            added, removed = self._stage(
                query,
                pa.Table.from_pandas(
                    frame.to_wkb(), schema=query.interchange, preserve_index=False
                ),
                pa.Table.from_pydict(
                    {"location_id": intervention.remove},
                    schema=pa.schema([query.interchange.field("location_id")]),
                ),
            )
            remaining = base.filter(base.location_id.notin(removed.location_id))
            # A source may expose narrower scalars or a concrete geometry subtype.
            remaining = remaining.mutate(
                **{
                    name: ops.Cast(remaining[name], to=dtype).to_expr()
                    for name, dtype in added.schema().items()
                    if remaining.schema()[name] != dtype
                }
            )
            self._effective_relation = remaining.union(added, distinct=False)
        return self._select_from(self._effective_relation, expression, schema)

    def _select_from(
        self, relation: ir.Table, expression: ir.Table, schema: ProviderSchema
    ) -> gpd.GeoDataFrame:
        root = expression.op()
        while isinstance(root, ops.Filter):
            root = root.parent
        if not isinstance(root, ops.UnboundTable):
            message = "Managed selection requires a validated symbolic filter"
            raise TypeError(message)
        replacements = self._literal_replacements(expression, schema)
        replacements[root] = relation.op()
        bound = expression.op().replace(replacements).to_expr()
        return self._materialize(bound, schema)

    def close(self) -> None:
        """Release the execution's reference to its effective relation."""
        self._effective_relation = None


def decode_records(data: pa.Table, schema: ProviderSchema) -> gpd.GeoDataFrame:
    """Reconstruct native columns without coercing IDs or validating records.

    Returns:
        Declared native columns and decoded geometry for producer validation.

    Raises:
        TypeError: If geometry has no explicit binary interchange type.
        ValueError: If columns or geometry SRIDs differ from the declaration.
    """
    query = compile_query_schema(schema.schema)
    if data.column_names != query.interchange.names:
        message = "Managed acquisition must return every registered column"
        raise ValueError(message)
    geometry_type = data.schema.field("geometry").type
    if not (
        pa.types.is_binary(geometry_type) or pa.types.is_large_binary(geometry_type)
    ):
        message = "Managed acquisition must return explicitly typed binary geometry"
        raise TypeError(message)
    geometry = gpd.GeoSeries.from_wkb(data["geometry"].to_pylist(), crs=query.crs)
    srids = shapely.get_srid(geometry.to_numpy())
    expected_srid = pyproj.CRS(query.crs).to_epsg()
    if any(srid not in {-1, 0, expected_srid} for srid in srids):
        message = "Managed geometry SRID conflicts with the declared CRS"
        raise ValueError(message)
    if not data.num_rows:
        return schema.empty_frame()
    columns = {}
    for name, column in schema.schema.columns.items():
        if name == "geometry":
            continue
        values = data[name].to_pylist()
        dtype = column.dtype.type
        if name == "location_id":
            # Only an explicitly string-typed batch can use a string dtype:
            # malformed IDs must reach producer validation without coercion.
            if not (
                pa.types.is_string(data[name].type)
                or pa.types.is_large_string(data[name].type)
            ):
                dtype = "object"
        elif data[name].null_count:
            # Preserve nulls for the producer's nullability check, even when the
            # declared numpy dtype cannot represent them. Avoid float inference
            # for nullable integers: it can lose precision before reconstruction.
            dtype = {"int64": "Int64", "bool": "boolean"}.get(str(column.dtype), dtype)
        columns[name] = pd.Series(values, dtype=dtype)
    columns["geometry"] = geometry
    return gpd.GeoDataFrame(columns, geometry="geometry", crs=query.crs)[
        list(schema.schema.columns)
    ]
