"""Database access helpers exposed to plugin code."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.sdk.resources import BaseIO, IOConfig

if TYPE_CHECKING:
    from collections.abc import Mapping
if TYPE_CHECKING:
    import geopandas
    import pandas as pd


class DatabaseIO(BaseIO[IOConfig]):
    """Execute plugin SQL with managed synchronous connections."""

    def query_dataframe(
        self, sql: str, *, params: Mapping[str, object] | None = None
    ) -> pd.DataFrame:
        """Read a table using named bound SQL parameters.

        Returns:
            The query's ordinary tabular result.
        """
        raise NotImplementedError

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
        raise NotImplementedError
