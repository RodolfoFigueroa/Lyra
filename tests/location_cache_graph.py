"""Native mapping and provider fixtures for cache integration checks."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
from typing_extensions import override

from lyra.contracts.locations import LocationSelection
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.resources import ResourceScope
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import Places
from tests.cache_helpers import SCHEMA
from tests.graph_execution_helpers import Label

SELECTION = {"seed": {"provider": "cache_source", "ids": ["0"]}}


def setup_graph(
    tmp_path: Path,
    *,
    empty: bool = False,
    data_revision: str | None = None,
    events: list[str] | None = None,
) -> tuple[LocalRunner, TableCache, str, list[str], list[gpd.GeoDataFrame], list[bool]]:
    events = [] if events is None else events
    seen = []
    broken = [False, False]

    class Source(Places):
        id = "cache_source"

    class Target(Places):
        id = "cache_target"
        code_revision = data_revision

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            frame = super().resolve(ids)
            frame["count"] = 2
            return frame

    @metric(
        cache="full",
        name="source",
        code_revision="1",
        description="Support",
        outputs={"source": SCHEMA},
    )
    def source() -> pd.DataFrame:
        events.append("source")
        return pd.DataFrame({"value": [2]})

    @location_operation(
        cache="full",
        code_revision="1",
        name="make",
        description="Map source to targets",
        inputs={"source": LocationDependency(provider=Source.id)},
        target_provider=Target.id,
    )
    def make(source: gpd.GeoDataFrame) -> object:
        events.append("make")
        if broken[1]:
            return {source.location_id.iloc[0]: ["0"]}
        return {} if empty else dict.fromkeys(source.location_id, ("0",))

    @metric(
        cache="full",
        code_revision="1",
        name="consume",
        description="Consume",
        inputs={
            "location": LocationDependency(provider=Target.id),
            "table": Dependency("source", producer="source"),
        },
        outputs={"consume": SCHEMA},
    )
    def consume(
        location: gpd.GeoDataFrame, table: pd.DataFrame, parameters: Label
    ) -> pd.DataFrame:
        assert table.value.tolist() == [2]
        events.append(parameters.label)
        seen.append(location)
        if broken[0]:
            return pd.DataFrame({"wrong": [1]})
        return pd.DataFrame({"value": [len(location)]})

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": SCHEMA},
        inputs={
            "seed": LocationDependency(provider=Source.id),
            "table": Dependency("consume", producer="consume").bind(
                location=LocationDependency("make").bind(source=Input("seed"))
            ),
        },
    )
    def root(
        seed: gpd.GeoDataFrame, parameters: Label, table: pd.DataFrame
    ) -> pd.DataFrame:
        assert seed.location_id.tolist() == ["0"]
        assert parameters.label
        return table

    plugin = PluginDefinition(
        metrics=[root, consume, source],
        location_operations=[make],
        resources={"source": Source, "target": Target},
    )
    runner = LocalRunner.from_definitions({"p": plugin}, artifact_dir=tmp_path)
    cache = TableCache(tmp_path)
    executor = GraphExecutor(runner.catalog, runner.catalog.implementations, Mock())
    prepared = prepare_execution(
        executor.catalog,
        {"metric": "root", "inputs": SELECTION, "parameters": {"label": "a"}},
    )
    root_use = executor.validate_plan(prepared)
    operation = root_use.bindings["inputs.table.locations.location"]
    scope = ResourceScope(plugin.resources)
    with scope.activate():
        resolver = LocationResolver(scope, providers=runner.catalog.provider_registry)
        source_values = resolver.resolve(
            {"source": LocationSelection(provider=Source.id, ids=["0"])}
        )
    invocation = executor.plugins["p"].prepare_resolved_invocation(
        "make", source_values
    )
    invocation = replace(
        invocation, parameters=invocation.inspect_parameters(), bound_use=operation
    )
    key = executor.persistent_key(invocation.identity)
    return runner, cache, key, events, seen, broken
