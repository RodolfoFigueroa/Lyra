"""Canonical eligibility is validated without executing handlers."""

from typing import Any, cast

import geopandas as gpd
import pytest
from lyra.sdk import Locations, PluginDefinition, location_operation
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.dependencies import (
    DependencyManifest,
    LiteralArgument,
    LocationDependencyManifest,
)
from lyra.sdk.models.plugin import (
    NODE_MANIFEST_ADAPTER,
    LocationInputDeclaration,
    LocationOperationManifest,
    PluginInfo,
)

from tests.test_location_operations import OUTPUT, definition
from tests.test_plugin_graph import manifest, node


def selector() -> LocationOperationManifest:
    result = (
        definition()
        .manifest(plugin=PluginInfo(name="test", version="1"), factory="test:create")
        .nodes[0]
    )
    assert result.kind == "location_operation"
    return result


def test_defaults_resources_and_context_are_eligible() -> None:
    operation = selector()
    operation.canonical = True
    producer = node("records")
    producer.parameters_schema = operation.parameters_schema
    compiled = compile_catalog({"test": manifest("test", operation, producer)})
    (edge,) = compiled.canonical_edges
    assert edge.operation == operation.name
    assert edge.source == operation.location_inputs["source"].contract
    assert edge.target == operation.output_contract


def test_required_selector_parameter_is_rejected() -> None:
    operation = selector()
    operation.canonical = True
    operation.inputs.clear()
    schema = cast("dict[str, Any]", operation.parameters_schema)
    schema["required"] = ["count"]
    schema["properties"]["count"].pop("default")
    with pytest.raises(ValueError, match="unsatisfied parameters"):
        compile_catalog({"test": manifest("test", operation)})


def test_ordinary_operations_have_no_edges() -> None:
    operation = selector()
    operation.inputs.clear()
    assert not compile_catalog({"test": manifest("test", operation)}).canonical_edges


def test_transitive_parameter_supply_is_checked() -> None:
    operation = selector()
    operation.canonical = True
    operation.inputs = {"data": DependencyManifest(producer="middle", output="middle")}
    middle = node("middle")
    leaf = node("leaf")
    for dependency in (middle, leaf):
        dependency.parameters_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "Count"}},
            "required": ["count"],
            "additionalProperties": False,
        }
    middle.inputs = {"data": DependencyManifest(producer="leaf", output="leaf")}
    declarations = {"test": manifest("test", operation, middle, leaf)}
    assert len(compile_catalog(declarations).canonical_edges) == 1
    schema = cast("dict[str, Any]", operation.parameters_schema)
    schema["properties"]["count"].pop("default")
    with pytest.raises(ValueError, match=r"unsatisfied binding|unsatisfied parameters"):
        compile_catalog(declarations)
    operation.inputs["data"].parameters["count"] = LiteralArgument(value=3)
    assert len(compile_catalog(declarations).canonical_edges) == 1


def test_canonical_shape_is_validated() -> None:
    operation = selector().model_dump()
    operation.update(canonical=True, location_inputs={})
    with pytest.raises(ValueError, match="exactly one"):
        NODE_MANIFEST_ADAPTER.validate_python(operation)


def test_decorator_captures_canonical_status() -> None:
    contract = selector().output_contract
    assert contract is not None

    @location_operation(
        name="canonical_places",
        description="Select places",
        canonical=True,
        locations={"source": contract},
        output_contract=contract,
        output=OUTPUT,
    )
    def select(source: Locations) -> gpd.GeoDataFrame:
        return source.to_geodataframe()

    plugin = PluginDefinition(location_operations=[select])
    declaration = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    assert declaration.nodes[0].kind == "location_operation"
    assert declaration.nodes[0].canonical
    described = plugin.describe("canonical_places").manifest
    assert described.kind == "location_operation"
    assert described.canonical
    assert len(compile_catalog({"test": declaration}).canonical_edges) == 1


def test_noncanonical_internal_operation_remains_off_routing_graph() -> None:
    operation = selector()
    operation.canonical = True
    helper = selector()
    helper.name = "helper"
    helper.inputs.clear()
    receiver = node("receiver")
    receiver.location_inputs = operation.location_inputs.copy()
    receiver.parameters_schema = operation.parameters_schema
    operation.inputs = {
        "data": DependencyManifest(
            producer="receiver",
            output="receiver",
            locations={"source": LocationDependencyManifest(operation="helper")},
        )
    }
    compiled = compile_catalog({"test": manifest("test", operation, helper, receiver)})
    assert [edge.operation for edge in compiled.canonical_edges] == [operation.name]
    assert compiled.graph.location_dependencies[operation.name]


def test_selected_route_cannot_depend_on_its_receiving_metric() -> None:
    operation = selector()
    operation.canonical = True
    operation.inputs.clear()
    source = operation.location_inputs["source"].contract
    target = source.model_copy(
        update={
            "family": source.family.model_copy(update={"collection": "test:target"})
        }
    )
    operation.output_contract = target
    receiver = node("receiver")
    receiver.location_inputs = dict(operation.location_inputs)
    receiver.location_inputs = {"source": LocationInputDeclaration(contract=target)}
    receiver.parameters_schema = operation.parameters_schema
    helper = operation.model_copy(deep=True)
    helper.name = "helper"
    helper.canonical = False
    operation.inputs = {
        "data": DependencyManifest(
            producer="receiver",
            output="receiver",
            locations={"source": LocationDependencyManifest(operation="helper")},
        )
    }
    with pytest.raises(ValueError, match=r"Execution plan cycle.*receiver.*source"):
        compile_catalog({"test": manifest("test", operation, helper, receiver)})


def test_conversion_cycle_is_not_an_execution_cycle() -> None:
    forward = selector()
    forward.inputs.clear()
    forward.canonical = True
    source = forward.location_inputs["source"].contract
    target = source.model_copy(update={"source": "other"})
    forward.output_contract = target
    reverse = forward.model_copy(deep=True)
    reverse.name = "reverse"
    reverse.location_inputs = {"source": LocationInputDeclaration(contract=target)}
    reverse.output_contract = source
    compiled = compile_catalog({"test": manifest("test", forward, reverse)})
    assert compiled.routes[source.reference, target.reference] == (forward.name,)
    assert compiled.routes[target.reference, source.reference] == (reverse.name,)
    assert compiled.routes[source.reference, source.reference] == ()
