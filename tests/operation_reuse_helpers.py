"""Isolated direct-source operation workload with observable acquisition."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import Parameters, Places
from tests.cache_helpers import SCHEMA


@dataclass
class WorkloadOptions:
    target_cache: bool = False
    policy: CachePolicy = "full"
    derived: bool = False
    independent: bool = False
    metric_cache: CachePolicy = "off"
    retain_result: bool = False


def workload(
    tmp_path: Path,
    *,
    target_cache: bool = False,
    policy: CachePolicy = "full",
    derived: bool = False,
    independent: bool = False,
) -> tuple[Callable[..., ExecutionOutcome], list, list, dict]:
    return configured_workload(
        tmp_path, WorkloadOptions(target_cache, policy, derived, independent)
    )


def configured_workload(
    tmp_path: Path, options: WorkloadOptions
) -> tuple[Callable[..., ExecutionOutcome], list, list, dict]:
    target_cache, policy = options.target_cache, options.policy
    derived, independent = options.derived, options.independent
    metric_cache, retain_result = options.metric_cache, options.retain_result
    events = []
    seen = []
    mapping = {}

    class Source(Places):
        id = "sources"

        @override
        def setup(self) -> None:
            events.append("source setup")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(("sources", frozenset(ids)))
            return super().resolve(ids)

    class Target(Places):
        id = "targets"
        cache = target_cache

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(("targets", frozenset(ids)))
            return super().resolve(ids)

    @location_operation(
        name="mapping",
        description="Map source groups",
        canonical=True,
        cache=policy,
        inputs={"source": LocationDependency(provider=Source.id)},
        target_provider=Target.id,
    )
    def operation(source: gpd.GeoDataFrame, *, parameters: Parameters) -> dict:
        events.append(("mapping", frozenset(source.location_id)))
        assert parameters.count >= 0
        return (
            {key: mapping[key] for key in source.location_id if key in mapping}
            if independent
            else mapping.copy()
        )

    @metric(
        name="consumer",
        cache=metric_cache,
        description="Consume target records",
        inputs={"places": LocationDependency(provider=Target.id)},
        outputs={"result": SCHEMA},
    )
    def consumer(places: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
        seen.append(places)
        return pd.DataFrame({"value": [parameters.count]})

    @metric(
        name="derived",
        cache=metric_cache,
        description="Consume explicitly bound operation records",
        inputs={
            "source": LocationDependency(provider=Source.id),
            "places": LocationDependency(operation="mapping").bind(
                source=Input("source")
            ),
        },
        outputs={"result": SCHEMA},
    )
    def derived_consumer(
        source: gpd.GeoDataFrame, places: gpd.GeoDataFrame, parameters: Parameters
    ) -> pd.DataFrame:
        assert set(places.source_location_id).issubset(source.location_id)
        return consumer(places, parameters)

    definition = PluginDefinition(
        metrics=[consumer, derived_consumer],
        location_operations=[operation],
        resources={"source": Source, "target": Target},
    )
    runner = LocalRunner.from_definitions({"test": definition})
    executor = GraphExecutor(runner.catalog, runner.catalog.implementations, Mock())
    executor.retain_result = retain_result
    executor.store = SQLiteStore(tmp_path / "values.sqlite")
    executor.provider_records = ProviderRecords(executor.store)

    def run(
        ids: tuple[str, ...] = ("a", "b"), parameters: dict | None = None
    ) -> ExecutionOutcome:
        request = {
            "metric": "derived" if derived else "consumer",
            "inputs": {
                "source" if derived else "places": {
                    "provider": Source.id,
                    "ids": list(ids),
                }
            },
        }
        if parameters is not None:
            request["parameters"] = parameters
        return executor.execute(prepare_execution(runner.catalog, request))

    return run, events, seen, mapping
