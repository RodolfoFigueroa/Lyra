"""Working tables cannot alter fresh, retained, or assembled native values."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.local import LocalRunner
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration, frame
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def output_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "location_id": column("string"),
            "row_key": column("string"),
            "count": column("Int64", nullable=True),
            "flag": column("boolean", nullable=True),
            "geometry": column(Geometry(crs=4326)),
        },
        strict=True,
        unique=["location_id", "row_key"],
    )


def mutate(value: pd.DataFrame) -> None:
    value.attrs.setdefault("nested", {"values": [1]})["values"].append(99)
    value.attrs["provider_scope"] = "forged"
    value.attrs["computation"] = "forged"
    if not value.empty:
        value.loc[:, "location_id"] = "forged"
        value.loc[:, "count"] = 99
        if "row_key" in value:
            value.loc[:, "row_key"] = "forged"
    if isinstance(value, gpd.GeoDataFrame):
        value.geometry = value.geometry.translate(xoff=100)
        value.set_crs(3857, allow_override=True, inplace=True)


def check(value: pd.DataFrame, ids: set[str], *, empty: bool) -> None:
    assert isinstance(value, gpd.GeoDataFrame)
    assert value.crs == "EPSG:4326"
    assert value.active_geometry_name == "geometry"
    assert str(value["count"].dtype) == "Int64"
    assert str(value.flag.dtype) == "boolean"
    assert str(value.row_key.dtype).startswith("string")
    assert set(value.location_id) == (set() if empty else ids)
    if not empty:
        rows = value.set_index("location_id")
        assert rows.flag.isna().all()
        for owner in ids:
            assert rows.loc[owner, "row_key"] == "row"
            assert rows.loc[owner, "geometry"] == Point(0, 0)
            assert rows.loc[owner, "count"] == 1
    if "nested" in value.attrs:
        assert value.attrs["nested"] == {"values": [1]}


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_mutating_consumers_and_repeated_reads(
    tmp_path: Path, policy: CachePolicy, *, derived: bool, empty: bool
) -> None:
    produced = []
    acquired = []
    work = []

    class Places(LocationProvider):
        id = "places"
        cache = True
        schema = declaration()

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            acquired.append(set(ids))
            result = frame().iloc[[0] * len(ids)].reset_index(drop=True)
            result["location_id"] = ids
            result.attrs = {"nested": {"values": [1]}}
            produced.append(result)
            return result

    @location_operation(
        name="forward",
        description="Forward original owners",
        cache=policy,
        inputs={"source": LocationDependency(provider="places")},
        target_provider="places",
    )
    def forward(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        owners = list(source.location_id)
        mutate(source)
        return {owner: (owner,) for owner in owners}

    @metric(
        name="leaf",
        description="Produce native nullable spatial rows",
        cache=policy,
        inputs={"places": LocationDependency(provider="places", primary=True)},
        outputs={"data": output_schema()},
    )
    def leaf(places: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        owners = list(places.location_id)
        work.append(set(owners))
        assert places.crs == "EPSG:4326"
        assert places["count"].tolist() == [1] * len(owners)
        if derived:
            assert set(places.source_location_id) == set(owners)
        mutate(places)
        ids = [] if empty else owners
        result = gpd.GeoDataFrame(
            {
                "location_id": pd.Series(ids, dtype="string"),
                "row_key": pd.Series(["row"] * len(ids), dtype="string"),
                "count": pd.Series([1] * len(ids), dtype="Int64"),
                "flag": pd.Series([None] * len(ids), dtype="boolean"),
            },
            geometry=gpd.GeoSeries([Point(0, 0)] * len(ids), crs=4326),
        )
        result.attrs = {"nested": {"values": [1]}}
        produced.append(result)
        return result

    binding = (
        LocationDependency(operation="forward").bind(source=Input("places"))
        if derived
        else Input("places")
    )

    @metric(
        name="root",
        description="Independent consumers of one producer",
        inputs={
            "places": LocationDependency(provider="places", primary=True),
            "left": Dependency("data", producer="leaf").bind(places=binding),
            "right": Dependency("data", producer="leaf").bind(places=binding),
        },
        outputs={"data": output_schema()},
    )
    def root(
        places: gpd.GeoDataFrame, left: gpd.GeoDataFrame, right: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        ids = set(places.location_id)
        check(left, ids, empty=empty)
        mutate(left)
        for retained in produced:
            mutate(retained)
        check(right, ids, empty=empty)
        return right

    runner = LocalRunner.from_definitions(
        {
            "p": PluginDefinition(
                metrics=[leaf, root],
                location_operations=[forward],
                resources={"places": Places},
            )
        },
        result_store_path=tmp_path / "values.sqlite",
    )
    for iteration, ids in enumerate((["a"], ["a", "b"], ["a", "b"])):
        work.clear()
        acquired.clear()
        result = runner.run_development(
            "root",
            inputs={"places": {"provider": "places", "ids": ids}},
        )
        check(result.tables["data"], set(ids), empty=empty)
        expected_work = {
            "off": [[{"a"}], [{"a", "b"}], [{"a", "b"}]],
            "full": [[{"a"}], [{"a", "b"}], []],
            "per_location": [[{"a"}], [{"b"}], []],
        }
        assert work == expected_work[policy][iteration]
        assert acquired == [[{"a"}], [{"b"}], []][iteration]
        mutate(result.tables["data"])
        assert result.metadata.result_ref is not None
        with ResultStore(
            SQLiteStore(tmp_path / "values.sqlite", read_only=True)
        ) as store:
            captured = store.read_location_input(result.metadata.result_ref, "places")
            assert set(captured.location_id) == set(ids)
            assert captured.crs == "EPSG:4326"
            assert captured["count"].tolist() == [1] * len(ids)
            assert all(geometry == Point(0, 0) for geometry in captured.geometry)
            first = store.read_result(result.metadata.result_ref)
            check(first.tables["data"], set(ids), empty=empty)
            mutate(first.tables["data"])
            first.metadata.outputs.clear()
            again = store.read_result(result.metadata.result_ref)
            assert again.metadata.outputs
            check(again.tables["data"], set(ids), empty=empty)
