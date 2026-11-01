"""Native execution and filesystem cache costs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from lyra.sdk import (
    LocalRunner,
    LocationCollection,
    Locations,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.preparation import prepare_execution
from typing_extensions import override

from benchmarks.workloads import workload
from tests.location_helpers import node_contract, output_schema
from tests.test_location_resolution import Provider, frame_features, selection


class ExecutionSuite:
    """Execute reusable graphs through the public local interface."""

    version = "1"
    params: ClassVar[list[str]] = [
        "chain-small",
        "chain-large",
        "diamond-small",
        "diamond-large",
    ]
    param_names: ClassVar[list[str]] = ["case"]

    def setup(self, case: str) -> None:
        """Compile once before repeated local executions."""
        shape, size = case.split("-")
        rows = 1 if size == "small" else 1000
        self.work = workload(rows, shape)
        self.runner = LocalRunner.from_definitions(
            {"benchmark-fixture": self.work.plugin}
        )

    def time_local(self, _case: str) -> None:
        """Include invocation preparation, resource scope, and validation."""
        self.runner.run("root", {})


class CacheSuite:
    """Measure genuinely empty, populated, and revision-invalidated caches."""

    version = "1"
    params: ClassVar[list[str]] = ["empty", "warm", "revision"]
    param_names: ClassVar[list[str]] = ["state"]
    number = 1
    warmup_time = 0

    def setup(self, state: str) -> None:
        """Reset storage for each individual measurement."""
        self.directory = TemporaryDirectory(prefix="lyra-benchmark-")
        self.work = workload(1000, "diamond")
        self.runner = LocalRunner.from_definitions(
            {"benchmark-fixture": self.work.plugin}, cache_dir=Path(self.directory.name)
        )
        self.revision = "first"
        if state != "empty":
            self.runner.run("root", {}, cache_revision=self.revision)
        if state == "revision":
            self.revision = "second"
        self.work.calls.clear()

    def time_execute(self, _state: str) -> None:
        """Execute one invocation with the prepared cache state."""
        self.runner.run("root", {}, cache_revision=self.revision)

    def teardown(self, _state: str) -> None:
        """Remove all temporary cache artifacts."""
        self.directory.cleanup()


class CanonicalExecutionSuite:
    """Measure accepted native and routed batches without request preparation."""

    version = "1"
    params: ClassVar[list[list[object]]] = [["native", "routed"], [1, 1000], [1, 2]]
    param_names: ClassVar[list[str]] = ["routed", "rows", "inputs"]

    def setup(self, routed: str, rows: int, inputs: int) -> None:
        """Compile and accept a deterministic in-process workload."""
        source = node_contract()
        target = node_contract("bench:target", "point", output_schema(original=str))

        @location_operation(
            name="select",
            description="Select",
            canonical=True,
            locations={"source": source},
            output_contract=target,
            output=output_schema(original=str),
        )
        def select(source: Locations) -> gpd.GeoDataFrame:
            return source.to_geodataframe()

        @metric(
            name="one",
            description="One",
            locations={"left": target},
            outputs={"result": pa.DataFrameSchema({})},
        )
        def one(left: Locations) -> pd.DataFrame:
            return pd.DataFrame(index=range(len(left)))

        @metric(
            name="two",
            description="Two",
            locations={"left": target, "right": target},
            outputs={"result": pa.DataFrameSchema({})},
        )
        def two(left: Locations, right: Locations) -> pd.DataFrame:
            return pd.DataFrame(index=range(len(left) + len(right)))

        class MemoryProvider(Provider):
            collections = (
                *Provider.collections,
                LocationCollection(
                    name="bench:target", unit_types=("point",), crs="EPSG:4326"
                ),
            )
            contracts = (*Provider.contracts, target)
            outputs: ClassVar[dict] = {
                **Provider.outputs,
                ("bench:target", "point"): output_schema(original=str),
            }

            @override
            def resolve(
                self, contract: LocationContract, ids: list[str]
            ) -> gpd.GeoDataFrame:
                return frame_features(ids)

        plugin = PluginDefinition(
            metrics=[one, two],
            location_operations=[select],
            resources={"locations": MemoryProvider},
            location_provider="locations",
        )
        runner = LocalRunner.from_definitions({"bench": plugin})
        self.executor = GraphExecutor(
            runner.catalog.graph,
            {"bench": plugin},
            Mock(),
            location_provider=runner.catalog.location_provider,
        )
        ids = [str(i) for i in range(rows)]
        value = (
            selection(*ids)
            if routed == "routed"
            else selection(*ids, collection="bench:target", unit_type="point")
        )
        self.prepared = prepare_execution(
            runner.catalog,
            {
                "metric": "one" if inputs == 1 else "two",
                "inputs": dict.fromkeys(
                    ["left"] if inputs == 1 else ["left", "right"], value
                ),
            },
        )

    def time_execute(self, _routed: str, _rows: int, _inputs: int) -> None:
        """Acquire and execute an already accepted route."""
        self.executor.execute(self.prepared)
