"""Accepted native and routed location workloads for in-process execution."""

from dataclasses import dataclass
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from typing_extensions import override

from lyra.contracts.accepted import PreparedExecution
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_resolution import Provider, frame_features, selection


@dataclass
class CanonicalWorkload:
    executor: GraphExecutor
    prepared: PreparedExecution


def canonical_workload(
    routed: str, rows: int, inputs: int, *, revision: str | None = None
) -> CanonicalWorkload:
    """Build a fresh executor and accept the requested batch against its catalog."""

    class Source(Provider):
        code_revision = "updated" if revision == "source" else "1"

    class Target(Provider):
        id = "bench_target"
        code_revision = "updated" if revision == "target" else "1"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            return frame_features(ids)

    @location_operation(
        code_revision="1",
        name="select",
        description="Select",
        canonical=True,
        inputs={"source": LocationDependency(provider=Source.id)},
        target=Target.id,
    )
    def select(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return source.copy(deep=True)

    @metric(
        code_revision="1",
        name="one",
        description="One",
        inputs={"left": LocationDependency(provider=Target.id)},
        outputs={"result": pa.DataFrameSchema({})},
    )
    def one(left: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(index=range(len(left)))

    @metric(
        code_revision="1",
        name="two",
        description="Two",
        inputs={
            "left": LocationDependency(provider=Target.id),
            "right": LocationDependency(provider=Target.id),
        },
        outputs={"result": pa.DataFrameSchema({})},
    )
    def two(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(index=range(len(left) + len(right)))

    plugin = PluginDefinition(
        metrics=[one, two],
        location_operations=[select],
        resources={"source": Source, "locations": Target},
    )
    runner = LocalRunner.from_definitions({"bench": plugin})
    executor = GraphExecutor(runner.catalog, {"bench": BoundPlugin(plugin)}, Mock())
    ids = [str(i) for i in range(rows)]
    value = selection(*ids, provider=Source.id if routed == "routed" else Target.id)
    prepared = prepare_execution(
        runner.catalog,
        {
            "metric": "one" if inputs == 1 else "two",
            "inputs": dict.fromkeys(
                ["left"] if inputs == 1 else ["left", "right"], value
            ),
        },
    )
    return CanonicalWorkload(executor, prepared)
