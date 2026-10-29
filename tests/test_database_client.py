from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, Mock

import geopandas
import pandas as pd
import pytest
import shapely
from lyra.sdk.db import DatabaseIO
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.elements import TextClause

from lyra_app.db.client import DatabaseIOImplicit

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Engine


@pytest.mark.parametrize("spatial", [False, True])
def test_queries_forward_bound_parameters_and_release_connection(
    monkeypatch: pytest.MonkeyPatch, *, spatial: bool
) -> None:
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value
    result = geopandas.GeoDataFrame() if spatial else pd.DataFrame({"count": [2]})
    reader = Mock(return_value=result)
    monkeypatch.setattr(
        geopandas if spatial else pd,
        "read_postgis" if spatial else "read_sql_query",
        reader,
    )
    db: DatabaseIO = DatabaseIOImplicit(cast("Engine", engine))
    params = {"name": "O'Brien", "limit": 2}
    sql = "SELECT * FROM records WHERE name = :name LIMIT :limit"
    actual = (
        db.query_geodataframe(sql, params=params, geom_col="shape", crs="EPSG:6372")
        if spatial
        else db.query_dataframe(sql, params=params)
    )
    assert actual is result
    statement, connection = reader.call_args.args
    assert isinstance(statement, TextClause)
    assert str(statement) == sql
    assert connection is conn
    assert reader.call_args.kwargs == (
        {"params": params, "geom_col": "shape", "crs": "EPSG:6372"}
        if spatial
        else {"params": params}
    )
    engine.connect.return_value.__exit__.assert_called_once()
    engine.dispose.assert_not_called()


@pytest.mark.parametrize("spatial", [False, True])
def test_query_errors_propagate_and_release_connection(
    monkeypatch: pytest.MonkeyPatch, *, spatial: bool
) -> None:
    engine = MagicMock()
    error = OperationalError("SELECT", {}, Exception("unavailable"))
    monkeypatch.setattr(
        geopandas if spatial else pd,
        "read_postgis" if spatial else "read_sql_query",
        Mock(side_effect=error),
    )
    db: DatabaseIO = DatabaseIOImplicit(cast("Engine", engine))
    with pytest.raises(OperationalError) as caught:
        (db.query_geodataframe if spatial else db.query_dataframe)("SELECT")
    assert caught.value is error
    engine.connect.return_value.__exit__.assert_called_once()
    engine.dispose.assert_not_called()


@pytest.mark.parametrize("crs", [None, "EPSG:6372"])
@pytest.mark.parametrize("geometries", ["empty", "null", "no_srid", "srid"])
def test_spatial_reader_crs_behavior(
    monkeypatch: pytest.MonkeyPatch, geometries: str, crs: str | None
) -> None:
    point = shapely.Point(500000, 2100000)
    values = {
        "empty": [],
        "null": [None, None],
        "no_srid": [shapely.to_wkb(point)],
        "srid": [shapely.to_wkb(shapely.set_srid(point, 4326), include_srid=True)],
    }[geometries]
    reader = Mock(
        side_effect=[
            pd.DataFrame({"shape": values}),
            pd.DataFrame({"auth_name": ["EPSG"]}),
        ]
    )
    monkeypatch.setattr(pd, "read_sql", reader)
    engine = MagicMock()
    db: DatabaseIO = DatabaseIOImplicit(cast("Engine", engine))
    result = db.query_geodataframe(
        "SELECT shape FROM records", geom_col="shape", crs=crs
    )
    assert isinstance(result, geopandas.GeoDataFrame)
    assert result.geometry.name == "shape"
    expected = crs or ("EPSG:4326" if geometries == "srid" else None)
    assert result.crs == expected
    assert len(result) == len(values)
    if geometries in {"no_srid", "srid"}:
        assert result.geometry.iloc[0].equals(point)
    else:
        assert result.geometry.isna().all()
    assert reader.call_count == (2 if geometries == "srid" and crs is None else 1)


class FakeDatabase(DatabaseIO):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def query_dataframe(
        self, sql: str, *, params: Mapping[str, object] | None = None
    ) -> pd.DataFrame:
        self.calls.append((sql, params))
        return pd.DataFrame({"value": [params["value"] if params else sql]})

    def query_geodataframe(
        self,
        sql: str,
        *,
        params: Mapping[str, object] | None = None,
        geom_col: str = "geometry",
        crs: str | None = None,
    ) -> geopandas.GeoDataFrame:
        self.calls.append((sql, params))
        return geopandas.GeoDataFrame({geom_col: []}, geometry=geom_col, crs=crs)


def test_database_protocol_accepts_an_independent_fake() -> None:
    db: DatabaseIO = FakeDatabase()
    assert db.query_dataframe("SELECT :value", params={"value": 7}).iloc[0, 0] == 7
    spatial = db.query_geodataframe("SELECT", geom_col="shape", crs="EPSG:4326")
    assert spatial.empty
    assert spatial.geometry.name == "shape"
    assert spatial.crs == "EPSG:4326"
