"""PostGIS access owned by the census example plugin."""

from collections.abc import Mapping
from typing import Protocol, cast

import geopandas
import pandas as pd
import sqlalchemy
from lyra.sdk import BaseIO, IOConfig
from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, Engine
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


class CensusDatabaseConfig(IOConfig):
    """Connection settings for the census source."""

    host: str = Field(min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = Field(min_length=1)
    user: str = Field(min_length=1)
    pool_size: int = Field(default=1, ge=1)
    max_overflow: int = Field(default=0, ge=0)
    pool_timeout_seconds: float = Field(default=5, gt=0)
    connect_timeout_seconds: int = Field(default=5, gt=0)
    statement_timeout_ms: int = Field(default=300000, gt=0)
    pool_recycle_seconds: int = Field(default=900, gt=0)

    @field_validator("host", "name", "user")
    @classmethod
    def required_text(cls, value: str) -> str:
        """Normalize required connection fields.

        Returns:
            The trimmed connection field.

        Raises:
            ValueError: If a field is blank.
        """
        if not value.strip():
            message = "Connection fields must not be blank"
            raise ValueError(message)
        return value.strip()


class CensusDatabaseIO(BaseIO[CensusDatabaseConfig]):
    """Execute plugin SQL with managed synchronous connections."""

    config_model = CensusDatabaseConfig
    secret_names = ("password",)

    def setup(self) -> None:
        """Create a pool when execution first requests this resource."""
        config = self.config
        self._engine: Engine | None = sqlalchemy.create_engine(
            URL.create(
                "postgresql+psycopg",
                username=config.user,
                password=self.secrets["password"],
                host=config.host,
                port=config.port,
                database=config.name,
            ),
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout_seconds,
            pool_recycle=config.pool_recycle_seconds,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": config.connect_timeout_seconds,
                "options": f"-c statement_timeout={config.statement_timeout_ms}",
            },
        )

    def close(self) -> None:
        """Dispose acquired connections, including after execution failure."""
        engine = getattr(self, "_engine", None)
        self._engine = None
        if engine is not None:
            engine.dispose()

    def _require_engine(self) -> Engine:
        """Return the initialized engine.

        Raises:
            RuntimeError: If setup has not completed.
        """
        engine = getattr(self, "_engine", None)
        if engine is None:
            message = "Census resource is not initialized"
            raise RuntimeError(message)
        return engine

    def query_dataframe(
        self, sql: str, *, params: Mapping[str, object] | None = None
    ) -> pd.DataFrame:
        """Read a table using named bound SQL parameters.

        Returns:
            The query's ordinary tabular result.
        """
        with self.operation("query"), self._require_engine().connect() as conn:
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
        with self.operation("query"), self._require_engine().connect() as conn:
            return cast("_GeoDataFrameReader", geopandas.read_postgis)(
                text(sql), conn, params=params, geom_col=geom_col, crs=crs
            )
