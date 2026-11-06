from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd

from lyra.data.locations import Locations
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_helpers import node_contract, producer_result
from test_support.location_schema import column, contract, declaration, locations
from tests.cache_helpers import SCHEMA
from tests.graph_execution_helpers import Label


def setup_graph(
    tmp_path: Path, *, empty: bool = False
) -> tuple[LocalRunner, TableCache, str, list[str], list[Locations], list[bool]]:
    events = []
    seen = []
    broken = [False, False]
    output = contract(declaration(count=column("Int64", coerce=True)))

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [2]})

    @location_operation(
        output_contract=node_contract("example:places", "point", output),
        name="make",
        description="Make",
        output=output,
        inputs={"table": Dependency("source", producer="source")},
    )
    def make(table: pd.DataFrame) -> gpd.GeoDataFrame:
        events.append("make")
        if broken[1]:
            return producer_result(locations({"count": "invalid"}), output)
        return producer_result(
            locations() if empty else locations({"count": str(table.value[0])}), output
        )

    @metric(
        locations={"location": node_contract("example:places", "point", output)},
        name="consume",
        description="Consume",
        outputs={"consume": SCHEMA},
    )
    def consume(location: Locations, parameters: Label) -> pd.DataFrame:
        events.append(parameters.label)
        seen.append(location)
        if broken[0]:
            return pd.DataFrame({"wrong": [1]})
        return pd.DataFrame({"value": [len(location.ids)]})

    @metric(
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "table": Dependency(
                "consume",
                locations={"location": LocationDependency("make")},
                producer="consume",
            )
        },
    )
    def root(parameters: Label, table: pd.DataFrame) -> pd.DataFrame:
        assert parameters.label
        return table

    plugin = PluginDefinition(
        metrics=[root, consume, source], location_operations=[make]
    )
    runner = LocalRunner.from_definitions({"p": plugin}, artifact_dir=tmp_path)
    cache = TableCache(tmp_path)

    executor = GraphExecutor(
        LocalRunner.from_definitions({"p": plugin}).catalog,
        {"p": BoundPlugin(plugin)},
        Mock(),
    )
    key = executor.persistent_key(
        executor.plugins["p"].prepare_resolved_invocation("make", {}).identity
    )
    return runner, cache, key, events, seen, broken
