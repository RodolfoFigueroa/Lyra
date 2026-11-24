"""Direct relationship inspection remains bounded and edition-specific."""

import asyncio
from collections.abc import Callable
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.mcp.dependencies import inspect_dependencies
from lyra.server.mcp.models import GetMetricInput
from lyra.server.mcp.tools import InProcessLyraBackend
from test_support.accepted_sources import routed_request
from test_support.mcp import call_tool
from test_support.table_fixtures import value_frame, value_schema
from tests.live_graph_helpers import Parameters, plugins


def fixture(count: int = 3, *, private: bool = True) -> Discovery:
    @metric(
        name="measure_0",
        code_revision="1",
        description="Base",
        private=private,
        outputs={"value": value_schema()},
    )
    def base() -> pd.DataFrame:
        return value_frame()

    def consumer_factory() -> Callable:
        def consumer(table: pd.DataFrame) -> pd.DataFrame:
            return table

        return consumer

    metrics = [
        metric(
            name=f"measure_{index}",
            code_revision="1",
            description="Consume measurement",
            inputs={"table": Dependency("value", producer="measure_0")},
            outputs={"value": value_schema()},
        )(consumer_factory())
        for index in range(1, count)
    ]
    return Discovery(
        compile_live_catalog({"metrics": PluginDefinition(metrics=[base, *metrics])})
    )


def test_direct_and_private() -> None:
    view = fixture()
    result = inspect_dependencies(view, GetMetricInput(metric="measure_1"))
    assert len(result.relationships) == 1
    edge = result.relationships[0]
    assert edge.direction == "dependency"
    assert edge.producer.private
    assert edge.producer.name == "measure_0"
    assert edge.consumer_input == "table"
    assert edge.producer_output is not None
    with pytest.raises(KeyError):
        inspect_dependencies(view, GetMetricInput(metric="measure_0"))
    with pytest.raises(KeyError):
        inspect_dependencies(view, GetMetricInput(metric="measure"))


@pytest.mark.parametrize("count", [1, 4, 102])
def test_consumers_empty_and_truncation(count: int) -> None:
    view = fixture(count, private=False)
    arguments = GetMetricInput(metric="measure_0")
    result = inspect_dependencies(view, arguments)
    assert len(result.relationships) == min(count - 1, 50)
    assert result.omitted_relationships == max(count - 51, 0)
    assert all(edge.direction == "consumer" for edge in result.relationships)
    assert result == inspect_dependencies(view, arguments)


def test_declared_operations_and_second_hop() -> None:

    definitions = plugins()
    schema = value_schema()

    @metric(
        name="upstream",
        code_revision="1",
        description="Upstream",
        outputs={"rows": schema},
    )
    def upstream() -> pd.DataFrame:
        return value_frame()

    @metric(
        name="producer",
        private=True,
        description="Producer",
        code_revision="1",
        inputs={
            "places": LocationDependency(provider="counties"),
            "rows": Dependency("rows", producer="upstream"),
        },
        outputs={"value": schema},
    )
    def producer(
        places: gpd.GeoDataFrame, rows: pd.DataFrame, parameters: Parameters
    ) -> pd.DataFrame:
        del places, parameters
        return rows

    operations = [
        node.function
        for node in definitions["upstream"].declarations
        if node.kind == "location_operation"
    ]
    definitions["upstream"] = PluginDefinition(
        metrics=[upstream, producer], location_operations=operations
    )
    view = Discovery(compile_live_catalog(definitions))
    result = inspect_dependencies(view, GetMetricInput(metric="consumer"))
    assert sorted(
        (edge.producer.name, edge.consumer_input, edge.producer_output)
        for edge in result.relationships
    ) == [
        ("producer", "first", "value"),
        ("producer", "second", "value"),
        ("select", "inputs.first.locations.places", None),
        ("select", "inputs.second.locations.places", None),
    ]


def test_available_routes_are_not_dependencies() -> None:

    catalog, _ = routed_request()
    view = Discovery(catalog)
    assert not inspect_dependencies(view, GetMetricInput(metric="score")).relationships


def test_tool_dispatch() -> None:
    view = fixture()
    backend = InProcessLyraBackend(lambda: Mock(catalog=Mock(discovery=view)))
    result = asyncio.run(
        call_tool(
            backend,
            "lyra_get_metric_dependencies",
            {"metric": "measure_1"},
        )
    )
    assert not result.get("isError", False)
    assert result["structuredContent"]["relationships"]
    rejected = asyncio.run(
        call_tool(backend, "lyra_get_metric_dependencies", {"metric": "measure"})
    )
    assert rejected["isError"]


def test_named_outputs_and_registration_order() -> None:
    schema = value_schema()

    @metric(
        name="producer",
        code_revision="1",
        description="Two outputs",
        outputs={"first": schema, "second": schema},
    )
    def producer() -> dict[str, pd.DataFrame]:
        return {"first": value_frame(), "second": value_frame()}

    @metric(
        name="target",
        code_revision="1",
        description="Paired outputs",
        inputs={
            "z": Dependency("first", producer="producer"),
            "a": Dependency("second", producer="producer"),
        },
        outputs={"target": schema},
    )
    def target(z: pd.DataFrame, a: pd.DataFrame) -> pd.DataFrame:
        return pd.concat([z, a])

    @metric(
        name="consumer",
        private=True,
        code_revision="1",
        description="Private consumer",
        inputs={"value": Dependency("target", producer="target")},
        outputs={"result": schema},
    )
    def consumer(value: pd.DataFrame) -> pd.DataFrame:
        return value

    expected = [
        ("dependency", "producer", "second", "target", "a"),
        ("dependency", "producer", "first", "target", "z"),
        ("consumer", "target", "target", "consumer", "value"),
    ]
    for ordered_nodes in ((producer, target, consumer), (consumer, target, producer)):
        view = Discovery(
            compile_live_catalog({"test": PluginDefinition(metrics=ordered_nodes)})
        )
        result = inspect_dependencies(view, GetMetricInput(metric="target"))
        assert [
            (
                edge.direction,
                edge.producer.name,
                edge.producer_output,
                edge.consumer.name,
                edge.consumer_input,
            )
            for edge in result.relationships
        ] == expected
        assert result.relationships[-1].consumer.private
        assert result.omitted_relationships == 0
