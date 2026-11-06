from collections.abc import Mapping
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd

from lyra.contracts.resources import IOConfig
from lyra.sdk.resources import BaseIO


class QueryIO(BaseIO[IOConfig]):
    def query_dataframe(
        self, sql: str, *, params: Mapping[str, object] | None = None
    ) -> pd.DataFrame:
        raise NotImplementedError

    def query_geodataframe(
        self,
        sql: str,
        *,
        params: Mapping[str, object] | None = None,
        geom_col: str = "geometry",
        crs: str | None = None,
    ) -> gpd.GeoDataFrame:
        raise NotImplementedError


def database_override(client: Mock) -> type[QueryIO]:
    class FakeQueryIO(QueryIO):
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

    return FakeQueryIO


def census_override(provider: type[BaseIO], client: Mock) -> type[BaseIO]:
    def setup(self: BaseIO) -> None:
        self.__dict__["query_dataframe"] = client.query_dataframe
        self.__dict__["query_geodataframe"] = client.query_geodataframe

    def close() -> None:
        return None

    return type(
        "FakeCensus",
        (provider,),
        {
            "config_model": IOConfig,
            "secret_names": (),
            "setup": setup,
            "close": staticmethod(close),
        },
    )
