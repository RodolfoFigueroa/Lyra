"""Bounded shortest routes over concrete contract identities."""

from typing import Any, cast

import pytest
from lyra.sdk.catalog import (
    AmbiguousRoutesError,
    CanonicalEdge,
    compile_catalog,
    compile_routes,
)
from lyra.sdk.location_contracts import LocationContract

from tests.test_canonical_operations import selector
from tests.test_plugin_graph import manifest


def contract(name: str) -> LocationContract:
    return selector().location_contracts["source"].model_copy(update={"source": name})


def edges(pairs: list[tuple[str, str]]) -> tuple[CanonicalEdge, ...]:
    return tuple(
        CanonicalEdge(f"op_{i}", "source", contract(a), contract(b))
        for i, (a, b) in enumerate(pairs)
    )


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
        operation.location_contracts = {"source": edge.source}
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
    source = operation.location_contracts["source"].reference
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
