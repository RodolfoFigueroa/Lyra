"""Native metric fixtures shared by execution and selection tests."""

from collections.abc import Callable
from unittest.mock import Mock

import geopandas as gpd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.sdk import Dependency, LocationDependency, PluginDefinition, metric
from test_support.location_schema import column
from tests.node_declaration_helpers import Tracts


def schema(*, spatial: bool = False) -> pa.DataFrameSchema:
    columns = {
        "location_id": column(str),
        "row_key": column(str),
        "value": pa.Column(
            "Int64",
            nullable=True,
            checks=pa.Check(lambda s: (s >= 0).all(), name="nonnegative"),
            description="Value",
            metadata={"unit": None},
        ),
    }
    if spatial:
        columns["geometry"] = column(Geometry)
    return pa.DataFrameSchema(columns, strict=True, unique=["location_id", "row_key"])


def setup(
    handler: Callable, *others: Callable, ids: tuple[str, ...] = ("a", "b", "c")
) -> tuple[GraphExecutor, PreparedResolvedInvocation]:
    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[handler, *others], resources={"tracts": Tracts}
            )
        }
    )
    plugin = runner.catalog.implementations["test"]
    node = plugin.node_manifest("findings")
    assert node is not None
    locations = {}
    for name, declaration in node.location_inputs.items():
        selected = ["reference"] if name == "reference" else list(ids)
        frame = gpd.GeoDataFrame(
            {"location_id": selected}, geometry=[Point(0, 0)] * len(selected), crs=4326
        )
        locations[name] = NativeLocationInput(frame, declaration.contract.reference)
    invocation = plugin.prepare_resolved_invocation("findings", locations)
    executor = GraphExecutor(runner.catalog, Mock())
    return executor, invocation


def declaration(
    outputs: dict[str, pa.DataFrameSchema],
    *,
    primary: bool = True,
    dependencies: dict[str, Dependency] | None = None,
) -> Callable:
    return metric(
        name="findings",
        description="Findings",
        code_revision="1",
        inputs={
            "main": LocationDependency(provider="tracts", primary=primary),
            "reference": LocationDependency(provider="tracts"),
            **(dependencies or {}),
        },
        outputs=outputs,
    )
