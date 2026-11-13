"""Source and target acquisitions borrow one scope through later consumers."""

from typing import Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from typing_extensions import override

from lyra.contracts.locations import LocationSelection
from lyra.runtime.execution import GraphExecutionError, RunState
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.resources import ResourceScope
from lyra.sdk import LocationDependency, PluginDefinition, location_operation, metric
from lyra.sdk.resources import BaseIO, Resource
from test_support.location_operations import Places
from test_support.operation_execution import OperationExecutor


def resource_runner(
    failure: str | None,
    events: list[str],
    instances: list[BaseIO],
    retained: list[gpd.GeoDataFrame],
) -> LocalRunner:
    class Database(BaseIO):
        @override
        def setup(self) -> None:
            instances.append(self)
            events.append("database setup")

        @override
        def close(self) -> None:
            events.append("database close")

    class Source(Places):
        id = "source"
        database: Annotated[Database, Resource("database")]

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            assert self.database is instances[-1]
            events.append(f"{self.id} resolve")
            frame = super().resolve(ids)
            retained.append(frame)
            return frame

        @override
        def close(self) -> None:
            assert "database close" not in events
            events.append(f"{self.id} close")

    class Target(Source):
        id = "target"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            frame = super().resolve(ids)
            fail_at(failure, "resolve")
            return frame

    @location_operation(
        name="select",
        description="Select targets",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Source.id)},
        target_provider=Target.id,
    )
    def select(renamed: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        fail_at(failure, "handler")
        return dict.fromkeys(renamed.location_id, ("x",))

    @metric(
        name="later",
        description="Consume the shared dependency after acquisition",
        code_revision="1",
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def later(database: Annotated[Database, Resource("database")]) -> pd.DataFrame:
        assert database is instances[-1]
        assert "database close" not in events
        events.append("consumer")
        fail_at(failure, "consumer")
        return pd.DataFrame()

    return LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[later],
                location_operations=[select],
                resources={"source": Source, "target": Target, "database": Database},
            )
        }
    )


@pytest.mark.parametrize("failure", [None, "handler", "resolve", "consumer"])
def test_shared_transitive_resource_lives_until_last_consumer(
    failure: str | None,
) -> None:
    events: list[str] = []
    instances: list[BaseIO] = []
    retained: list[gpd.GeoDataFrame] = []
    runner = resource_runner(failure, events, instances, retained)
    plugin = runner.catalog.implementations["test"]
    executor = OperationExecutor(runner.catalog, runner.catalog.implementations, Mock())
    for _ in range(2):
        events.clear()
        state = RunState()
        scope = ResourceScope(plugin.resources)

        def invoke(scope: ResourceScope, state: RunState) -> None:
            with scope.activate():
                resolver = LocationResolver(
                    scope, providers=runner.catalog.provider_registry
                )
                state.resolver = resolver
                source = resolver.resolve(
                    {"source": LocationSelection(provider="source", ids=["a"])}
                )
                invocation = plugin.prepare_resolved_invocation("select", source)
                result = executor.dispatch(invocation, state, Mock())
                retained[-1].loc[0, "count"] = 999
                assert result.targets.frame["count"].tolist() == [4]
                plugin.invoke(
                    plugin.prepare_resolved_invocation("later", {}), {}, Mock()
                )

        try:
            invoke(scope, state)
        except (GraphExecutionError, RuntimeError):
            if failure is None:
                raise
            if failure in {"handler", "resolve"}:
                assert not state
                assert "consumer" not in events
            else:
                assert len(state) == 1
        else:
            assert failure is None
        assert events[-1] == "database close"
        assert events.count("source close") == 1
        assert events.count("target close") == (0 if failure == "handler" else 1)
        state.clear()
        assert not state
        assert state.resolver is None
    assert len(instances) == 2
    assert instances[0] is not instances[1]


def fail_at(failure: str | None, stage: str) -> None:
    if failure == stage:
        message = f"{stage} failed"
        raise RuntimeError(message)
