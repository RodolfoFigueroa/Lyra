"""Native handler inputs preserve captured records throughout one resource scope."""

from typing import Annotated

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point
from typing_extensions import override

from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    metric,
)
from lyra.sdk.resources import BaseIO, Resource
from tests.provider_execution_helpers import RECORDS, RESULT


def resources(
    failure: str | None,
    events: list[str],
    instances: list[BaseIO],
    retained: list[gpd.GeoDataFrame],
) -> dict[str, type[BaseIO]]:
    class Database(BaseIO):
        @override
        def setup(self) -> None:
            instances.append(self)
            events.append("database setup")

        @override
        def close(self) -> None:
            events.append("database close")

    class Unused(BaseIO):
        @override
        def setup(self) -> None:
            pytest.fail("Unused resource initialized")

    class Records(LocationProvider):
        id = "records"
        schema = RECORDS
        database: Annotated[Database, Resource("database")]

        @override
        def setup(self) -> None:
            events.append("provider setup")
            if failure == "setup":
                message = "setup failure"
                raise RuntimeError(message)

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            assert self.database is instances[-1]
            events.append("resolve")
            result = gpd.GeoDataFrame(
                {"location_id": ids, "value": [1] * len(ids)},
                geometry=[Point(0, 0)] * len(ids),
                crs=4326,
            )
            result.attrs = {"nested": {"values": [1]}, "provider_scope": "forged"}
            retained.append(result)
            return result

        @override
        def close(self) -> None:
            assert events[-1] != "database close"
            events.append("provider close")
            if failure in {"cleanup", "both"}:
                message = "cleanup failure"
                raise RuntimeError(message)

    return {"provider": Records, "database": Database, "unused": Unused}


def runner_fixture(
    failure: str | None,
    events: list[str],
    instances: list[BaseIO],
    retained: list[gpd.GeoDataFrame],
) -> LocalRunner:
    registered = resources(failure, events, instances, retained)

    def check(frame: gpd.GeoDataFrame) -> None:
        assert frame.location_id.tolist() == ["a"]
        assert frame.value.tolist() == [1]
        assert frame.geometry.iloc[0] == Point(0, 0)
        assert frame.crs == "EPSG:4326"

    @metric(
        name="upstream",
        description="Mutate working inputs",
        code_revision="1",
        inputs={
            "left": LocationDependency(provider="records"),
            "right": LocationDependency(provider="records"),
        },
        outputs={"result": RESULT},
    )
    def upstream(
        left: gpd.GeoDataFrame,
        right: gpd.GeoDataFrame,
        database: Annotated[BaseIO, Resource("database")],
    ) -> pd.DataFrame:
        assert database is instances[-1]
        check(left)
        check(right)
        left.loc[0, "location_id"] = "changed"
        left.loc[0, "value"] = 9
        left["geometry"] = gpd.GeoSeries([Point(2, 2)], crs=left.crs)
        left.set_crs(3857, allow_override=True, inplace=True)
        left.attrs["nested"]["values"].append(2)
        check(right)
        assert right.attrs["nested"]["values"] == [1]
        for frame in retained:
            check(frame)
            assert frame.attrs["nested"]["values"] == [1]
            frame.loc[0, "location_id"] = "provider mutation"
            frame.loc[0, "value"] = 99
        if failure in {"handler", "both"}:
            message = "handler failure"
            raise RuntimeError(message)
        return pd.DataFrame({"total": [1]})

    @metric(
        name="root",
        description="Consume original records",
        code_revision="1",
        inputs={
            "left": LocationDependency(provider="records"),
            "right": LocationDependency(provider="records"),
            "data": Dependency("result", producer="upstream").bind(
                left=Input("left"), right=Input("right")
            ),
        },
        outputs={"result": RESULT},
    )
    def root(
        left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, data: pd.DataFrame
    ) -> pd.DataFrame:
        check(left)
        check(right)
        return data

    return LocalRunner.from_definitions(
        {
            "providers": PluginDefinition(resources=registered),
            "metrics": PluginDefinition(
                metrics=[root, upstream], resources={"database": registered["database"]}
            ),
        }
    )


@pytest.mark.parametrize("failure", [None, "handler", "setup", "cleanup", "both"])
def test_provider_and_consumers_share_scope_and_isolate_records(
    failure: str | None,
) -> None:
    events: list[str] = []
    instances: list[BaseIO] = []
    retained: list[gpd.GeoDataFrame] = []

    runner = runner_fixture(failure, events, instances, retained)
    for _ in range(2):
        events.clear()
        retained.clear()
        if failure is None:
            result = runner.run(
                "root",
                inputs={
                    name: {"provider": "records", "ids": ["a"]}
                    for name in ("left", "right")
                },
            ).tables
            assert result["result"].total.tolist() == [1]
        else:
            with pytest.raises(GraphExecutionError) as error:
                runner.run(
                    "root",
                    inputs={
                        name: {"provider": "records", "ids": ["a"]}
                        for name in ("left", "right")
                    },
                )
            if failure == "both":
                assert "handler failure" in str(error.value)
        assert events[-2:] == ["provider close", "database close"]
        assert events.count("provider close") == events.count("database close") == 1
        # One authoritative record per provider ID across two named selections.
        assert events.count("resolve") == (0 if failure == "setup" else 1)
    assert len(instances) == 2
    assert instances[0] is not instances[1]
