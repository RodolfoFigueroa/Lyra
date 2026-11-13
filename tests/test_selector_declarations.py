"""Selector registration and planning are computational and acquisition-free."""

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from types import FunctionType
from unittest.mock import Mock

import geopandas as gpd
import ibis.expr.types as ir
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Input, LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.planning.catalog import assemble_catalog, compile_live_catalog
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.runtime.planning.node_declarations import (
    NodeDeclaration,
    compile_node_declarations,
)
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition
from test_support.location_operations import Places
from tests.selector_helpers import ManagedProvider, plugin


class Parameters(MetricParameters):
    limit: int = Field(2, description="Maximum targets")


class OtherParameters(MetricParameters):
    limit: int = 2


def select(source: gpd.GeoDataFrame, relation: ir.Table) -> ir.Table:
    pytest.fail(f"Unexpected selector invocation: {source}, {relation}")


def select_parameters(
    source: gpd.GeoDataFrame, relation: ir.Table, *, parameters: Parameters
) -> ir.Table:
    pytest.fail(f"Unexpected selector invocation: {source}, {relation}, {parameters}")


def mapping(source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> None:
    pytest.fail(f"Unexpected mapping invocation: {source}, {candidates}")


def mapping_parameters(
    records: gpd.GeoDataFrame, pool: gpd.GeoDataFrame, /, *, parameters: Parameters
) -> None:
    pytest.fail(f"Unexpected mapping invocation: {records}, {pool}, {parameters}")


@pytest.mark.parametrize("parameterized", [False, True])
def test_capture_discovery_and_fixed_target(
    *, parameterized: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("Unexpected acquisition"))
    monkeypatch.setattr(ManagedProvider, "__init__", forbidden)
    monkeypatch.setattr(ManagedProvider, "setup", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    handler = mapping_parameters if parameterized else mapping
    selector = select_parameters if parameterized else select
    # Decorators capture a fresh function without altering shared test callbacks.
    handler = clone(handler)
    definition = plugin(handler, selector)
    assert definition.implementation("select_targets").target_selector is selector
    bound = bind_native_definitions({"test": definition})["test"]
    implementation = bound.implementation("select_targets")
    assert implementation.target_selector is selector
    assert implementation.parameters is (Parameters if parameterized else None)
    assert bound.declarations[0].target_selector is selector
    manifest = implementation.manifest
    assert manifest.kind == "location_operation"
    assert manifest.primary_input == "declared_source"
    assert set(manifest.location_inputs) == {"declared_source"}
    assert manifest.target_provider == "managed"
    assert manifest.has_target_selector
    if parameterized:
        schema = json.loads(manifest.model_dump_json())["parameters_schema"]
        assert schema["properties"]["limit"]["default"] == 2
    transport = bound.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    encoded = transport.model_dump_json()
    assert PluginManifest.model_validate_json(encoded) == transport
    assert json.loads(encoded)["nodes"][0]["has_target_selector"] is True
    catalog = compile_live_catalog({"test": definition})
    assert catalog.graph.nodes["select_targets"].node == manifest
    binding = LocationDependency("select_targets").bind(
        declared_source=Input("upstream")
    )
    assert binding.locations == {"declared_source": Input("upstream")}
    assert implementation.target == "managed"
    forbidden.assert_not_called()


def clone(function: Callable) -> Callable:
    assert isinstance(function, FunctionType)
    result = FunctionType(
        function.__code__, function.__globals__, closure=function.__closure__
    )
    result.__annotations__ = dict(function.__annotations__)
    return result


def mismatched(
    source: gpd.GeoDataFrame, table: ir.Table, *, parameters: OtherParameters
) -> None:
    pass


def keyword_table(source: gpd.GeoDataFrame, *, table: ir.Table) -> None:
    pass


def extra(source: gpd.GeoDataFrame, table: ir.Table, extra: object) -> None:
    pass


async def asynchronous(source: gpd.GeoDataFrame, table: ir.Table) -> None:
    pass


def generator(_source: gpd.GeoDataFrame, table: ir.Table) -> Iterator[ir.Table]:
    yield table


@pytest.mark.parametrize(
    ("handler", "selector"),
    [
        (mapping, select_parameters),
        (mapping_parameters, select),
        (mapping_parameters, mismatched),
        (mapping, keyword_table),
        (mapping, extra),
        (mapping, asynchronous),
        (mapping, generator),
        (mapping, object()),
        (mapping, mapping),
        (select, select),
        (lambda: None, select),
    ],
)
def test_invalid_pairs_fail_in_declaration_and_compiler(
    handler: Callable, selector: Callable
) -> None:
    with pytest.raises(PluginDefinitionError):
        plugin(clone(handler), selector)
    declaration = NodeDeclaration(
        "operation",
        handler,
        inputs={"source": LocationDependency(provider=Places.id)},
        kind="location_operation",
        target="managed",
        target_selector=selector,
    )
    providers = PluginDefinition(
        resources={"places": Places, "managed": ManagedProvider}
    )
    with pytest.raises(PluginDefinitionError):
        compile_node_declarations({"test": providers}, [declaration])


def test_incapable_target_rejected_at_each_binding_entry_point() -> None:
    definition = plugin(clone(mapping), select, Places.id)
    with pytest.raises(ValueError, match="managed query capability"):
        definition.describe("select_targets")
    with pytest.raises(ValueError, match="managed query capability"):
        bind_native_definitions({"test": definition})


def test_serialized_plan_requires_capable_fixed_target() -> None:
    catalog = compile_live_catalog({"test": plugin(clone(mapping), select)})
    manifest = catalog.manifests["test"].model_copy(deep=True)
    manifest.providers = tuple(
        provider.model_copy(update={"query_capable": False, "query_schema": None})
        for provider in manifest.providers
    )
    with pytest.raises(ValueError, match="managed query capability"):
        assemble_catalog(catalog.graph, {"test": manifest})
    declaration = NodeDeclaration("metric", mapping, target_selector=select)
    with pytest.raises(ValueError, match="Only operations"):
        compile_node_declarations(
            {"test": PluginDefinition(resources={"managed": ManagedProvider})},
            [replace(declaration, function=lambda: None)],
        )


@pytest.mark.parametrize("override_target", [False, True])
def test_forwarded_parameters_and_operation_derived_source(
    *, override_target: bool
) -> None:
    def upstream(records: gpd.GeoDataFrame) -> None:
        pytest.fail(str(records))

    def consumer(
        seed: gpd.GeoDataFrame, result: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> None:
        pytest.fail(f"{seed}, {result}, {parameters}")

    source = LocationDependency("upstream").bind(original=Input("seed"))
    result = LocationDependency(
        "selected", parameters={"limit": Input("parameters.limit")}
    ).bind(declared=source)
    if override_target:
        result = result.bind(target=Input("seed"))
    declarations = [
        NodeDeclaration(
            "upstream",
            upstream,
            inputs={"original": LocationDependency(provider=Places.id)},
            kind="location_operation",
            target=Places.id,
        ),
        NodeDeclaration(
            "selected",
            mapping_parameters,
            inputs={"declared": LocationDependency(provider=Places.id)},
            kind="location_operation",
            target="managed",
            target_selector=select_parameters,
        ),
        NodeDeclaration(
            "consumer",
            consumer,
            inputs={"seed": LocationDependency(provider=Places.id), "result": result},
        ),
    ]
    providers = {
        "test": PluginDefinition(
            resources={"places": Places, "managed": ManagedProvider}
        )
    }
    if override_target:
        with pytest.raises(ValueError, match="Unknown receiving location binding"):
            compile_node_declarations(providers, declarations)
        return
    compiled = compile_node_declarations(providers, declarations)
    assert compiled["consumer"].locations["result"].scope.provider_id == "managed"
    assert compiled["selected"].declaration.target == "managed"
    assert compiled["selected"].parameters is Parameters
    assert compiled["consumer"].declaration.inputs["result"].parameters == {
        "limit": Input("parameters.limit")
    }
