"""Bounded shortest routes over concrete contract identities."""

from typing import Any, cast

import geopandas as gpd
import pytest

from lyra.contracts.plugins import LocationInputDeclaration, PluginInfo
from lyra.data.locations import Locations
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import (
    AmbiguousRoutesError,
    CanonicalEdge,
    compile_catalog,
    compile_routes,
)
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract, edges
from test_support.location_operations import OUTPUT, MemoryIO, select_places
from test_support.plugin_graph import manifest, node


def test_shortcuts_cycles_identity_and_unreachable() -> None:
    graph = edges([("a", "b"), ("b", "c"), ("c", "a"), ("a", "c"), ("a", "a")])
    routes = compile_routes(graph, [contract("isolated").reference])
    a, b, c, isolated = (contract(n).reference for n in ("a", "b", "c", "isolated"))
    assert routes[a, a] == ()
    assert routes[a, c] == ("op_3",)
    assert routes[b, a] == ("op_1", "op_2")
    assert routes[isolated, isolated] == ()
    assert (a, isolated) not in routes
    assert dict(routes) == dict(compile_routes(reversed(graph), [isolated]))


@pytest.mark.parametrize(
    "pairs",
    [[("a", "b"), ("a", "b")], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]],
)
def test_ties_are_order_independent(pairs: list[tuple[str, str]]) -> None:
    graph = edges(pairs)
    with pytest.raises(AmbiguousRoutesError) as first:
        compile_routes(graph)
    with pytest.raises(AmbiguousRoutesError) as second:
        compile_routes(reversed(graph))
    assert first.value.ambiguities == second.value.ambiguities
    assert all(len(paths) == 2 for paths in first.value.ambiguities.values())


def test_many_paths_keep_only_two_witnesses() -> None:
    pairs = [
        (f"{i}_{a}", f"{i + 1}_{b}")
        for i in range(25)
        for a in range(2)
        for b in range(2)
    ]
    with pytest.raises(AmbiguousRoutesError) as error:
        compile_routes(edges(pairs))
    paths = error.value.ambiguities[
        contract("0_0").reference, contract("25_0").reference
    ]
    assert len(paths) == 2
    assert all(len(path) == 25 for path in paths)


def test_catalog_routes_span_plugins_and_reject_ties() -> None:
    operations = []
    for edge in edges([("a", "b"), ("b", "c")]):
        operation = selector()
        operation.name = edge.operation
        operation.inputs.clear()
        operation.canonical = True
        operation.location_inputs = {
            "source": LocationInputDeclaration(contract=edge.source)
        }
        operation.output_contract = edge.target
        operations.append(operation)
    declarations = {str(i): manifest(str(i), op) for i, op in enumerate(operations)}
    routes = compile_catalog(declarations).routes
    assert routes[contract("a").reference, contract("c").reference] == ("op_0", "op_1")
    assert routes == compile_catalog(dict(reversed(list(declarations.items())))).routes
    duplicate = operations[0].model_copy(deep=True)
    duplicate.name = "parallel"
    declarations["extra"] = manifest("extra", duplicate)
    with pytest.raises(AmbiguousRoutesError):
        compile_catalog(declarations)


def test_native_catalog_inputs_have_zero_routes_without_edges() -> None:
    operation = selector()
    operation.inputs.clear()
    compiled = compile_catalog({"native": manifest("native", operation)})
    source = operation.location_inputs["source"].contract.reference
    assert compiled.routes[source, source] == ()
    assert operation.output_contract is not None
    target = operation.output_contract.reference
    assert compiled.routes[target, target] == ()
    if source != target:
        assert (source, target) not in compiled.routes
    with pytest.raises(TypeError):
        cast("Any", compiled.routes)[source, source] = ("changed",)


def test_same_geography_does_not_merge_source_or_edition() -> None:
    first = contract("first")
    other_source = contract("second")
    other_edition = first.model_copy(update={"edition": "another"})
    references = [c.reference for c in (first, other_source, other_edition)]
    routes = compile_routes((), references)
    assert set(routes) == {(ref, ref) for ref in references}


@pytest.mark.parametrize(
    ("pairs", "preference"),
    [
        ([("a", "b"), ("a", "b"), ("a", "b")], ("op_2",)),
        ([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")], ("op_1", "op_3")),
    ],
)
def test_preferences_resolve_ties(
    pairs: list[tuple[str, str]], preference: tuple[str, ...]
) -> None:
    graph = edges(pairs)
    routes = compile_routes(graph, preferences=[preference])
    pair = graph[0].source.reference, graph[-1].target.reference
    assert routes[pair] == preference
    assert routes == compile_routes(
        reversed(graph), preferences=[preference, preference]
    )
    surviving = [edge for edge in graph if edge.operation in preference]
    assert compile_routes(surviving, preferences=[preference])[pair] == preference


@pytest.mark.parametrize(
    ("preference", "match"),
    [
        ((), "nonempty"),
        (("missing",), "Unknown or noncanonical"),
        (("op_0", "op_0"), "Disconnected"),
        (("op_0", "op_1"), "not shortest"),
        (("op_0", "op_1", "op_3"), "not shortest"),
    ],
)
def test_invalid_preferences(preference: tuple[str, ...], match: str) -> None:
    graph = edges([("a", "b"), ("b", "c"), ("a", "c"), ("c", "a")])
    with pytest.raises(ValueError, match=match):
        compile_routes(graph, preferences=[preference])


def test_conflicting_preferences_and_remaining_ties() -> None:
    graph = edges([("a", "b"), ("a", "b"), ("b", "c")])
    with pytest.raises(ValueError, match="Conflicting"):
        compile_routes(graph, preferences=[("op_0",), ("op_1",)])
    with pytest.raises(AmbiguousRoutesError):
        compile_routes(graph, preferences=[("op_0",)])


def test_catalog_preference_serialization_and_conflicts() -> None:
    operations = []
    for edge in edges([("a", "b"), ("a", "b")]):
        operation = selector()
        operation.name = edge.operation
        operation.inputs.clear()
        operation.canonical = True
        operation.location_inputs = {
            "source": LocationInputDeclaration(contract=edge.source)
        }
        operation.output_contract = edge.target
        operations.append(operation)
    declarations = {str(i): manifest(str(i), op) for i, op in enumerate(operations)}
    for name in ("consumer_one", "consumer_two"):
        consumer = node(name, spatial={"places"})
        consumer.location_inputs = {
            "places": LocationInputDeclaration(contract=contract("b"))
        }
        consumer.location_inputs["places"].source_bindings = (contract("b").reference,)
        declarations[name] = manifest(name, consumer)
    declarations["0"].route_preferences = (("op_1",),)
    declarations = {
        key: type(value).model_validate_json(value.model_dump_json())
        for key, value in declarations.items()
    }
    pair = contract("a").reference, contract("b").reference
    assert compile_catalog(declarations).routes[pair] == ("op_1",)
    declarations["1"].route_preferences = (("op_0",),)
    with pytest.raises(ValueError, match="Conflicting"):
        compile_catalog(declarations)
    declarations["1"].route_preferences = ()
    assert declarations["1"].nodes[0].kind == "location_operation"
    declarations["1"].nodes[0].canonical = False
    with pytest.raises(ValueError, match="noncanonical"):
        compile_catalog(declarations)


def test_edition_endpoints() -> None:
    a, b = contract("a"), contract("b")
    edition = b.model_copy(update={"edition": "next"})
    graph = (
        CanonicalEdge("first", "source", a, edition),
        CanonicalEdge("second", "source", a, edition),
    )
    routes = compile_routes(graph, [b.reference], preferences=[("second",)])
    assert routes[a.reference, edition.reference] == ("second",)
    assert (a.reference, b.reference) not in routes


def test_registration_captures_operation_references() -> None:

    plugin = PluginDefinition(
        location_operations=[select_places],
        resources={"memory": MemoryIO},
        route_preferences=[(select_places,)],
    )
    captured = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    assert captured.route_preferences == (("select_places",),)
    assert (
        type(captured).model_validate_json(captured.model_dump_json()).route_preferences
        == captured.route_preferences
    )


def test_local_runner_preserves_route_preferences() -> None:
    operations = []
    for name in ("first", "second"):

        def select(source: Locations) -> gpd.GeoDataFrame:
            return source.to_geodataframe()

        operation = location_operation(
            name=name,
            description="Select places",
            output=OUTPUT,
            output_contract=contract("b"),
            locations={"source": contract("a")},
            canonical=True,
        )(select)
        operations.append(operation)
    plugin = PluginDefinition(
        location_operations=operations, route_preferences=[(operations[1],)]
    )
    local = LocalRunner.from_definitions({"test": plugin})
    assert local.catalog.routes[contract("a").reference, contract("b").reference] == (
        "second",
    )
