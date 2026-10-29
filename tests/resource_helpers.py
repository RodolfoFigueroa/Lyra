from collections.abc import Mapping
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
from lyra.sdk import DatabaseIO, IOConfig


def database_override(client: Mock) -> type[DatabaseIO]:
    class FakeDatabaseIO(DatabaseIO):
        def __init__(self, config: IOConfig, secrets: Mapping[str, str]) -> None:
            super().__init__(config, secrets)
            self.client = client

        def query_dataframe(
            self, sql: str, *, params: Mapping[str, object] | None = None
        ) -> pd.DataFrame:
            return self.client.query_dataframe(sql, params=params)

        def query_geodataframe(
            self,
            sql: str,
            *,
            params: Mapping[str, object] | None = None,
            geom_col: str = "geometry",
            crs: str | None = None,
        ) -> gpd.GeoDataFrame:
            return self.client.query_geodataframe(
                sql, params=params, geom_col=geom_col, crs=crs
            )

    return FakeDatabaseIO
