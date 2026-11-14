"""Canonical eligibility is validated without executing handlers."""

from typing import Any, cast

import geopandas as gpd
import pytest

from lyra.contracts.dependencies import (
    Dependency,
    LocationDependency,
)
from lyra.contracts.plugins import (
    NODE_MANIFEST_ADAPTER,
    LocationInputDeclaration,
    PluginInfo,
)
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract as route_contract
from test_support.location_operations import Parameters, Places
from test_support.manifest_catalog import compile_catalog
from test_support.parameter_catalog import with_models
from test_support.plugin_graph import manifest, node


def test_defaults_and_reporting_context_are_eligible() -> None:
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
    schema["properties"].pop("count")
    with pytest.raises(ValueError, match="unsatisfied parameters"):
        compile_catalog({"test": manifest("test", operation)})


def test_ordinary_operations_have_no_edges() -> None:
    operation = selector()
    operation.inputs.clear()
    assert not compile_catalog({"test": manifest("test", operation)}).canonical_edges


def test_transitive_parameter_supply_is_checked() -> None:
    operation = selector()
    operation.canonical = True
    operation.inputs = {"data": Dependency(producer="middle", output="middle")}
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
    middle.inputs = {"data": Dependency(producer="leaf", output="leaf")}
    declarations = {"test": manifest("test", operation, middle, leaf)}
    assert len(compile_catalog(declarations).canonical_edges) == 1
    schema = cast("dict[str, Any]", operation.parameters_schema)
    schema["properties"].pop("count")
    with pytest.raises(
        ValueError, match=r"unsatisfied binding|unsatisfied parameters|no supply path"
    ):
        compile_catalog(declarations)
    operation.inputs["data"].parameters["count"] = 3
    assert len(compile_catalog(declarations).canonical_edges) == 1


def test_canonical_shape_is_validated() -> None:
    declaration = selector()
    operation = declaration.model_dump(exclude={"inputs"})
    operation["inputs"] = declaration.inputs
    operation.update(canonical=True, location_inputs={})
    with pytest.raises(ValueError, match="exactly one"):
        NODE_MANIFEST_ADAPTER.validate_python(operation)


def test_decorator_captures_canonical_status() -> None:
    contract = selector().output_contract
    assert contract is not None

    @location_operation(
        code_revision="1",
        name="canonical_places",
        description="Select places",
        canonical=True,
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def select(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return source

    plugin = PluginDefinition(
        location_operations=[select], resources={"places": Places}
    )
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
    receiver.scope = "location"
    receiver.primary_input = operation.primary_input
    receiver.parameters_schema = operation.parameters_schema
    operation.inputs = {
        "data": Dependency(
            producer="receiver",
            output="receiver",
        ).bind(source=LocationDependency(operation="helper"))
    }
    compiled = compile_catalog({"test": manifest("test", operation, helper, receiver)})
    assert [edge.operation for edge in compiled.canonical_edges] == [operation.name]
    assert compiled.graph.location_dependencies[operation.name]


def test_selected_route_cannot_depend_on_its_receiving_metric() -> None:
    operation = selector()
    operation.canonical = True
    operation.inputs.clear()
    source = operation.location_inputs["source"].contract
    target = route_contract("target")
    operation.output_contract = target
    receiver = node("receiver")
    receiver.location_inputs = dict(operation.location_inputs)
    receiver.location_inputs = {"source": LocationInputDeclaration(contract=target)}
    receiver.parameters_schema = operation.parameters_schema
    helper = operation.model_copy(deep=True)
    helper.name = "helper"
    helper.canonical = False
    operation.inputs = {
        "data": Dependency(
            producer="receiver",
            output="receiver",
        ).bind(source=LocationDependency(operation="helper"))
    }
    declaration = manifest("test", operation, helper, receiver)
    declaration.providers = tuple(
        ProviderDeclaration(resource=value.family.collection, contract=value)
        for value in (source, target)
    )
    declaration.resources = {value.resource: {} for value in declaration.providers}
    catalog = compile_catalog({"test": declaration})
    request = {
        "metric": "receiver",
        "inputs": {
            "source": {
                "provider": source.family.collection,
                "ids": ["one"],
            }
        },
    }
    with pytest.raises(
        PreparationError, match=r"Execution plan cycle.*receiver.*source"
    ):
        prepare_execution(catalog, request)
    request["inputs"]["source"]["provider"] = target.family.collection
    catalog = with_models(catalog, {"receiver": Parameters})
    assert prepare_execution(catalog, request).inputs[0].binding.operations == ()


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
