"""Bounded shortest routes over concrete contract identities."""

from typing import Any, cast

import geopandas as gpd
import pytest

from lyra.contracts.plugins import (
    PluginInfo,
    PluginInspection,
)
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import (
    AmbiguousRoutesError,
    compile_live_catalog,
)
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.canonical_routes import route_plugins
from test_support.location_operations import Places, select_places
from test_support.routing_plugins import conversion, provider
from tests.live_graph_helpers import operation as live_operation


def edges(pairs: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    return {f"op_{i}": pair for i, pair in enumerate(pairs)}


def test_shortcuts_cycles_identity_and_unreachable() -> None:
    graph = edges([("a", "b"), ("b", "c"), ("c", "a"), ("a", "c"), ("a", "a")])
    catalog = compile_live_catalog(route_plugins(graph, isolated=["isolated"]))
    a, b, c, isolated = (
        catalog.provider_registry[key].contract.reference
        for key in ("a", "b", "c", "isolated")
    )
    assert catalog.routes[a, a] == ()
    assert catalog.routes[a, c] == ("op_3",)
    assert catalog.routes[b, a] == ("op_1", "op_2")
    assert catalog.routes[isolated, isolated] == ()
    assert (a, isolated) not in catalog.routes
    assert (
        catalog.routes
        == compile_live_catalog(
            route_plugins(dict(reversed(graph.items())), isolated=["isolated"])
        ).routes
    )


@pytest.mark.parametrize(
    "pairs",
    [[("a", "b"), ("a", "b")], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]],
)
def test_ties_are_order_independent(pairs: list[tuple[str, str]]) -> None:
    graph = edges(pairs)
    with pytest.raises(AmbiguousRoutesError) as first:
        compile_live_catalog(route_plugins(graph))
    with pytest.raises(AmbiguousRoutesError) as second:
        compile_live_catalog(route_plugins(dict(reversed(graph.items()))))
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
        compile_live_catalog(route_plugins(edges(pairs)))
    paths = next(
        paths
        for (source, target), paths in error.value.ambiguities.items()
        if (source.collection, target.collection) == ("0_0", "25_0")
    )
    assert len(paths) == 2
    assert all(len(path) == 25 for path in paths)


def test_catalog_routes_span_plugins_and_reject_ties() -> None:
    definitions = {
        "providers": PluginDefinition(resources={key: provider(key) for key in "abc"}),
        "first": PluginDefinition(location_operations=[conversion("first", "a", "b")]),
        "second": PluginDefinition(
            location_operations=[conversion("second", "b", "c")]
        ),
    }
    catalog = compile_live_catalog(definitions)
    refs = {name: p.contract.reference for name, p in catalog.provider_registry.items()}
    assert catalog.routes[refs["a"], refs["c"]] == ("first", "second")
    assert (
        catalog.routes
        == compile_live_catalog(dict(reversed(definitions.items()))).routes
    )
    definitions["extra"] = PluginDefinition(
        location_operations=[conversion("parallel", "a", "b")]
    )
    with pytest.raises(AmbiguousRoutesError):
        compile_live_catalog(definitions)


def test_native_catalog_inputs_have_zero_routes_without_edges() -> None:
    catalog = compile_live_catalog(
        {
            "test": PluginDefinition(
                resources={key: provider(key) for key in "ab"},
                location_operations=[
                    live_operation("ordinary", "a", "b", canonical=False)
                ],
            )
        }
    )
    a, b = (catalog.provider_registry[key].contract.reference for key in "ab")
    assert catalog.routes == {(a, a): (), (b, b): ()}
    with pytest.raises(TypeError):
        cast("Any", catalog.routes)[a, b] = ("changed",)


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
    catalog = compile_live_catalog(route_plugins(graph, preferences=[preference]))
    pair = (
        catalog.provider_registry[pairs[0][0]].contract.reference,
        catalog.provider_registry[pairs[-1][1]].contract.reference,
    )
    assert catalog.routes[pair] == preference
    assert (
        compile_live_catalog(
            route_plugins(
                dict(reversed(graph.items())), preferences=[preference, preference]
            )
        ).routes
        == catalog.routes
    )
    surviving = {name: pair for name, pair in graph.items() if name in preference}
    assert (
        compile_live_catalog(route_plugins(surviving, preferences=[preference])).routes[
            pair
        ]
        == preference
    )


def test_partial_preference_leaves_other_pairs_ambiguous() -> None:
    graph = edges([("a", "b"), ("a", "b"), ("b", "c")])
    with pytest.raises(AmbiguousRoutesError):
        compile_live_catalog(route_plugins(graph, preferences=[("op_0",)]))


def test_catalog_preference_serialization_and_conflicts() -> None:
    first, second = (conversion(name, "a", "b") for name in ("first", "second"))
    definitions = {
        "one": PluginDefinition(
            location_operations=[first], route_preferences=[(second,)]
        ),
        "two": PluginDefinition(location_operations=[second]),
        "providers": PluginDefinition(resources={key: provider(key) for key in "ab"}),
    }
    catalog = compile_live_catalog(definitions)
    a, b = (catalog.provider_registry[key].contract.reference for key in "ab")
    assert catalog.routes[a, b] == ("second",)
    definitions["two"] = PluginDefinition(
        location_operations=[second], route_preferences=[(first,)]
    )
    with pytest.raises(ValueError, match="Conflicting"):
        compile_live_catalog(definitions)


def test_edition_endpoints() -> None:
    catalog = compile_live_catalog(
        route_plugins(
            {"first": ("a", "b_next"), "second": ("a", "b_next")},
            preferences=[("second",)],
            isolated=["b"],
        )
    )
    a, b, edition = (
        catalog.provider_registry[key].contract.reference
        for key in ("a", "b", "b_next")
    )
    assert catalog.routes[a, edition] == ("second",)
    assert (a, b) not in catalog.routes


def test_registration_captures_operation_references() -> None:

    plugin = PluginDefinition(
        location_operations=[select_places],
        resources={"places": Places},
        route_preferences=[(select_places,)],
    )
    captured = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    assert captured.route_preferences == (("select_places",),)
    assert (
        PluginInspection.model_validate_json(
            captured.model_dump_json()
        ).route_preferences
        == captured.route_preferences
    )


def test_local_runner_preserves_route_preferences() -> None:
    operations = []
    for name in ("first", "second"):

        def select(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            return source.copy(deep=True)

        operation = location_operation(
            code_revision="1",
            name=name,
            description="Select places",
            target_provider="b",
            inputs={"source": LocationDependency(provider="a")},
            canonical=True,
        )(select)
        operations.append(operation)

    class Source(Places):
        id = "a"

    class Target(Places):
        id = "b"

    plugin = PluginDefinition(
        location_operations=operations,
        route_preferences=[(operations[1],)],
        resources={"source": Source, "target": Target},
    )
    local = LocalRunner.from_definitions({"test": plugin})
    a, b = (
        local.catalog.provider_registry[name].contract.reference for name in ("a", "b")
    )
    assert local.catalog.routes[a, b] == ("second",)
