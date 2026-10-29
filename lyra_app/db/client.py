"""Generic database reads using the worker-owned connection pool."""

from collections.abc import Mapping
from typing import Protocol, cast

import geopandas
import pandas as pd
from lyra.sdk.db import DatabaseIO
from lyra.sdk.resources import IOConfig
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.elements import TextClause


# The readers delegate SQLAlchemy statements and database-supported parameter
# values to the connection. Their stubs restrict these more than the runtime.
class _DataFrameReader(Protocol):
    def __call__(
        self, sql: TextClause, con: Connection, *, params: Mapping[str, object] | None
    ) -> pd.DataFrame: ...


class _GeoDataFrameReader(Protocol):
    def __call__(
        self,
        sql: TextClause,
        con: Connection,
        *,
        params: Mapping[str, object] | None,
        geom_col: str,
        crs: str | None,
    ) -> geopandas.GeoDataFrame: ...


class DatabaseIOImplicit(DatabaseIO):
    """Execute plugin SQL with managed synchronous connections."""

    def __init__(self, engine: Engine) -> None:
        """Initialize database operations with the worker-owned engine."""
        super().__init__(IOConfig(), {})
        self._engine = engine

    def query_dataframe(
        self, sql: str, *, params: Mapping[str, object] | None = None
    ) -> pd.DataFrame:
        """Read a table using named bound SQL parameters.

        Returns:
            The query's ordinary tabular result.
        """
        with self.operation("query"), self._engine.connect() as conn:
            return cast("_DataFrameReader", pd.read_sql_query)(
                text(sql), conn, params=params
            )

    def query_geodataframe(
        self,
        sql: str,
        *,
        params: Mapping[str, object] | None = None,
        geom_col: str = "geometry",
        crs: str | None = None,
    ) -> geopandas.GeoDataFrame:
        """Read spatial SQL using GeoPandas' normal SRID inference.

        An explicit CRS labels coordinates without transforming them. Without
        supplied or inferred metadata, the CRS remains unset.

        Returns:
            The query's spatial table with the selected geometry column.
        """
        with self.operation("query"), self._engine.connect() as conn:
            return cast("_GeoDataFrameReader", geopandas.read_postgis)(
                text(sql), conn, params=params, geom_col=geom_col, crs=crs
            )
