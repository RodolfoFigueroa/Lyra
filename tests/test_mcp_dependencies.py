"""Direct relationship inspection remains bounded and edition-specific."""

import asyncio
from unittest.mock import Mock

import pytest
from jsonschema import validate
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.dependencies import Dependency, DependencyManifest, LocationDependency
from lyra.sdk.discovery import Discovery

from benchmarks.catalog import AcceptedSourcesSuite
from lyra_app.mcp.dependencies import inspect_dependencies
from lyra_app.mcp.models import TOOL_CONTRACTS_BY_NAME, GetMetricInput
from lyra_app.mcp.tools import InProcessLyraBackend, ToolCallError, execute_tool
from tests.test_accepted_sources import declarations
from tests.test_mixed_graph import operation
from tests.test_plugin_graph import manifest, node


def fixture(count: int = 3, *, private: bool = True) -> Discovery:
    suite = AcceptedSourcesSuite()
    suite.setup(count)
    nodes = suite.manifests["benchmark"].nodes
    for declaration in nodes:
        declaration.location_inputs = nodes[0].location_inputs.copy()
    assert nodes[0].kind == "metric"
    output = next(iter(nodes[0].outputs))
    for index, declaration in enumerate(nodes[1:], 1):
        declaration.inputs = {
            f"input_{index}": DependencyManifest(producer=nodes[0].name, output=output)
        }
    assert nodes[0].kind == "metric"
    nodes[0].private = private
    return Discovery(compile_catalog(suite.manifests))


def test_direct_and_private() -> None:
    view = fixture()
    result = inspect_dependencies(view, GetMetricInput(metric="measure", edition="1"))
    assert len(result.relationships) == 1
    edge = result.relationships[0]
    assert edge.direction == "dependency"
    assert edge.producer.private
    assert edge.producer.name == "measure_0"
    assert edge.consumer_input == "input_1"
    assert edge.producer_output is not None
    with pytest.raises(KeyError):
        inspect_dependencies(view, GetMetricInput(metric="measure", edition="0"))
    with pytest.raises(ValueError, match="explicit edition"):
        inspect_dependencies(view, GetMetricInput(metric="measure"))


@pytest.mark.parametrize("count", [1, 4, 102])
def test_consumers_empty_and_truncation(count: int) -> None:
    view = fixture(count, private=False)
    arguments = GetMetricInput(metric="measure", edition="0")
    result = inspect_dependencies(view, arguments)
    assert len(result.relationships) == min(count - 1, 50)
    assert result.omitted_relationships == max(count - 51, 0)
    assert all(edge.direction == "consumer" for edge in result.relationships)
    assert result == inspect_dependencies(view, arguments)


def test_declared_operations_and_second_hop() -> None:

    source = node("source")
    middle = node("middle", inputs={"first": Dependency("source", producer="source")})
    leaf = node("leaf", spatial={"places"})
    root = node(
        "root",
        inputs={
            "a": Dependency("middle", producer="middle"),
            "b": Dependency(
                "leaf",
                producer="leaf",
                locations={"places": LocationDependency("select")},
            ),
        },
    )
    view = Discovery(
        compile_catalog(
            {"test": manifest("test", source, middle, leaf, root, operation("select"))}
        )
    )
    result = inspect_dependencies(view, GetMetricInput(metric="root"))
    assert {edge.producer.name for edge in result.relationships} == {
        "middle",
        "leaf",
        "select",
    }
    location = next(
        edge for edge in result.relationships if edge.producer.name == "select"
    )
    assert location.producer_output is None
    assert location.consumer_input == "inputs.b.locations.places"


def test_available_routes_are_not_dependencies() -> None:

    metric, source, _, operations = declarations()
    metric.location_inputs["places"].source_bindings = (source.reference,)
    view = Discovery(compile_catalog({"test": manifest("test", metric, *operations)}))
    assert not inspect_dependencies(view, GetMetricInput(metric="score")).relationships


def test_tool_dispatch() -> None:
    view = fixture()
    backend = InProcessLyraBackend(lambda: Mock(catalog=Mock(discovery=view)))
    contract = TOOL_CONTRACTS_BY_NAME["lyra_get_metric_dependencies"]
    result = asyncio.run(
        execute_tool(
            contract.name,
            contract.input_model.model_validate({"metric": "measure", "edition": "1"}),
            backend,
            public_api_base_url="https://example.test",
        )
    )
    validate(result.model_dump(mode="json"), contract.output_schema)
    with pytest.raises(ToolCallError):
        asyncio.run(
            execute_tool(
                contract.name,
                contract.input_model.model_validate({"metric": "measure"}),
                backend,
                public_api_base_url="https://example.test",
            )
        )


def test_named_outputs_and_registration_order() -> None:
    producer = node("producer", outputs=("first", "second"))
    target = node(
        "target",
        inputs={
            "z": Dependency("first", producer="producer"),
            "a": Dependency("second", producer="producer"),
        },
    )
    consumer = node(
        "consumer", inputs={"value": Dependency("target", producer="target")}
    )
    consumer.private = True
    expected = [
        ("dependency", "producer", "second", "target", "a"),
        ("dependency", "producer", "first", "target", "z"),
        ("consumer", "target", "target", "consumer", "value"),
    ]
    for ordered_nodes in ((producer, target, consumer), (consumer, target, producer)):
        view = Discovery(compile_catalog({"test": manifest("test", *ordered_nodes)}))
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
