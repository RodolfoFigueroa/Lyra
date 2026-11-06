"""Accepted native and routed location workloads for in-process execution."""

from dataclasses import dataclass
from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from typing_extensions import override

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.locations import LocationContract
from lyra.contracts.providers import LocationCollection
from lyra.data.locations import Locations
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_helpers import node_contract, output_schema
from test_support.location_resolution import Provider, frame_features, selection


@dataclass
class CanonicalWorkload:
    executor: GraphExecutor
    prepared: PreparedExecution


def canonical_workload(
    routed: str, rows: int, inputs: int, *, revision: str | None = None
) -> CanonicalWorkload:
    """Build a fresh executor and accept the requested batch against its catalog."""
    source = node_contract()
    target = node_contract("bench:target", "point", output_schema(original=str))
    source.data_revision = "updated" if revision == "source" else None
    target.data_revision = "updated" if revision == "target" else None

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
        contracts = (
            *(
                contract.model_copy(update={"data_revision": source.data_revision})
                if contract.reference == source.reference
                else contract
                for contract in Provider.contracts
            ),
            target,
        )
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
    executor = GraphExecutor(
        runner.catalog,
        {"bench": BoundPlugin(plugin)},
        Mock(),
    )
    ids = [str(i) for i in range(rows)]
    value = (
        selection(*ids)
        if routed == "routed"
        else selection(*ids, collection="bench:target", unit_type="point")
    )
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
