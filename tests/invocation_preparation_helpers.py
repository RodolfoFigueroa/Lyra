"""Mixed bindings, ownership, and preparation through graph execution."""

from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa

from lyra.contracts.dependencies import Input
from lyra.data.locations import Locations
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.sdk.dependencies import Dependency, LocationDependency
from test_support.declarations import PluginDefinition, location_operation, metric
from test_support.location_helpers import (
    node_contract,
    producer_result,
)
from test_support.location_schema import column, contract, declaration, locations
from tests.graph_arguments_helpers import Producer

_DEFAULT = object()


def implementation(
    seen: list[tuple[str, Locations, Producer]] | None = None,
    operation_result: object = _DEFAULT,
) -> tuple[CompiledCatalog, dict[str, BoundPlugin]]:
    seen = [] if seen is None else seen
    output = contract(declaration(count=column(int, coerce=True)))

    @location_operation(
        code_revision="1",
        locations={"source": node_contract("example:places", "point", output)},
        output_contract=node_contract("example:places", "point", output),
        name="inner",
        description="Inner",
        output=output,
    )
    def inner(source: Locations, *, parameters: Producer) -> gpd.GeoDataFrame:
        seen.append(("inner", source, parameters))
        return (
            cast("gpd.GeoDataFrame", operation_result)
            if operation_result is not _DEFAULT
            else producer_result(locations({"count": "3"}), output)
        )

    @location_operation(
        code_revision="1",
        locations={"source": node_contract("example:places", "point", output)},
        output_contract=node_contract("example:places", "point", output),
        name="outer",
        description="Outer",
        output=output,
        inputs={"table": Dependency("leaf", producer="leaf")},
    )
    def outer(
        source: Locations, *, parameters: Producer, table: pd.DataFrame
    ) -> gpd.GeoDataFrame:
        assert table.empty
        seen.append(("outer", source, parameters))
        return source.to_geodataframe()

    @metric(
        code_revision="1",
        locations={"source": node_contract("example:places", "point", output)},
        name="leaf",
        description="Leaf",
        outputs={"leaf": pa.DataFrameSchema({})},
    )
    def leaf(source: Locations, parameters: Producer) -> pd.DataFrame:
        seen.append(("leaf", source, parameters))
        return pd.DataFrame()

    @metric(
        code_revision="1",
        locations={"source": node_contract("example:places", "point", output)},
        name="middle",
        description="Middle",
        outputs={"middle": pa.DataFrameSchema({})},
        inputs={"table": Dependency("leaf", producer="leaf")},
    )
    def middle(
        source: Locations, parameters: Producer, table: pd.DataFrame
    ) -> pd.DataFrame:
        seen.append(("middle", source, parameters))
        return table

    binding = LocationDependency(
        "outer",
        parameters={"year": 1980},
    ).bind(
        source=LocationDependency(
            "inner",
            parameters={"year": Input("parameters.year")},
        ).bind(source=Input("alias"))
    )

    @metric(
        code_revision="1",
        locations={
            "source": node_contract("example:places", "point", output),
            "alias": node_contract("example:places", "point", output),
        },
        name="root",
        description="Root",
        outputs={"root": pa.DataFrameSchema({})},
        inputs={
            "first": Dependency(
                "middle",
                parameters={"year": 1990},
                producer="middle",
            ).bind(source=binding),
            "second": Dependency(
                "middle",
                parameters={"year": 1990, "count": 2},
                producer="middle",
            ).bind(source=binding),
        },
    )
    def root(
        source: Locations,
        alias: Locations,
        parameters: Producer,
        first: pd.DataFrame,
        second: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(alias, Locations)
        seen.append(("root", source, parameters))
        pd.testing.assert_frame_equal(first, second)
        return first

    plugins = {
        "root": PluginDefinition(metrics=[root]),
        "tables": PluginDefinition(metrics=[middle, leaf]),
        "operations": PluginDefinition(location_operations=[inner, outer]),
    }
    return LocalRunner.from_definitions(plugins).catalog, {
        key: BoundPlugin(plugin) for key, plugin in plugins.items()
    }
