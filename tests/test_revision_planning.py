"""Revision closures and structural namespaces remain independently scoped."""

import json
from typing import Any, cast
from unittest.mock import Mock

import pytest

from lyra.contracts.dependencies import DependencyManifest, LocationDependencyManifest
from lyra.contracts.plugins import LocationInputDeclaration
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.planning.catalog import compile_catalog
from lyra.sdk.dependencies import Dependency
from test_support.accepted_sources import declarations
from test_support.plugin_graph import manifest, node


def test_selected_route_boundary_and_revision_only_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric, source, other, operations = declarations()
    metric.location_inputs["places"].source_bindings = (source.reference,)
    source.data_revision = "same"
    other.data_revision = "same"
    native = metric.location_inputs["places"].contract
    native.data_revision = "native"
    unrelated = node("unrelated")
    manifests = {"test": manifest("test", metric, unrelated, *operations)}
    first = compile_catalog(manifests)
    plan = first.revisions.plan("score", ((source.reference, ("select_0",)),))
    assert {b.contract for b in plan.calculation} == {native.reference, other.reference}
    assert {b.contract for b in plan.selection} == {source.reference, native.reference}
    assert len(plan.bindings) == 3
    assert first.revisions.nodes["unrelated"] == frozenset()
    source.data_revision = "changed"
    second = compile_catalog(manifests)
    assert first.computation.nodes == second.computation.nodes
    assert first.revisions.nodes["score"] == second.revisions.nodes["score"]
    assert first.revisions.nodes["select_0"] != second.revisions.nodes["select_0"]
    assert first.revisions.contracts[source.reference].revision == "same"
    with pytest.raises(TypeError):
        cast("Any", first.revisions.contracts)[source.reference] = None
    monkeypatch.setattr(
        "lyra.runtime.planning.computation.installed_version", lambda _: "changed"
    )
    assert compile_catalog(manifests).computation.nodes != first.computation.nodes


def test_transitive_dependencies_and_unversioned_target() -> None:
    _metric, source, _, operations = declarations()
    operation = operations[0]
    operation.canonical = False
    assert operation.output_contract is not None
    operation.output_contract.data_revision = None
    source.data_revision = "source"
    producer = node("producer", spatial={"places"})
    producer.location_inputs = {"places": LocationInputDeclaration(contract=source)}
    middle = node(
        "middle", inputs={"data": Dependency("producer", producer="producer")}
    )
    root = node("root", inputs={"data": Dependency("middle", producer="middle")})
    for consumer in (middle, root):
        consumer.location_inputs = producer.location_inputs.copy()
        consumer.parameters_schema = producer.parameters_schema
    compiled = compile_catalog(
        {"test": manifest("test", producer, middle, root, operation)}
    )
    assert compiled.revisions.nodes["root"] == compiled.revisions.nodes["producer"]
    assert {b.revision for b in compiled.revisions.nodes[operation.name]} == {
        None,
        "source",
    }
    before = compiled.computation.nodes
    source.meaning = "Changed structural meaning"
    assert (
        compile_catalog(
            {"test": manifest("test", producer, middle, root, operation)}
        ).computation.nodes
        != before
    )


def test_dependency_target_revision_reaches_metric() -> None:

    _metric, source, _, operations = declarations()
    operation = operations[0]
    operation.canonical = False
    assert operation.output_contract is not None
    operation.output_contract.data_revision = "target"
    source.data_revision = "source"
    producer = node("producer", spatial={"places"})
    producer.location_inputs = {
        "places": LocationInputDeclaration(contract=operation.output_contract)
    }
    root = node("root", spatial={"source"})
    root.location_inputs = {"source": LocationInputDeclaration(contract=source)}
    root.inputs = {
        "data": DependencyManifest(
            producer="producer",
            output="producer",
            locations={"places": LocationDependencyManifest(operation=operation.name)},
        )
    }
    compiled = compile_catalog({"test": manifest("test", root, producer, operation)})
    assert {b.revision for b in compiled.revisions.nodes["root"]} == {
        "source",
        "target",
    }


def test_invocation_keys_scope_native_and_selection_revisions() -> None:
    metric, source, other, operations = declarations()
    metric.location_inputs["places"].source_bindings = (source.reference,)

    def keys() -> dict[str, str]:
        catalog = compile_catalog({"test": manifest("test", metric, *operations)})
        executor = GraphExecutor(catalog, {}, Mock())
        return {
            name: executor.persistent_key(json.dumps({"node": name}))
            for name in catalog.graph.nodes
        }

    original = keys()
    source.data_revision = "shared"
    selected = keys()
    assert selected["score"] == original["score"]
    assert selected["select_0"] != original["select_0"]
    assert selected["select_1"] == original["select_1"]
    other.data_revision = "shared"
    scoped = keys()
    assert scoped["score"] != selected["score"]
    assert scoped["select_0"] == selected["select_0"]
    assert scoped["select_1"] != selected["select_1"]
    metric.location_inputs["places"].contract.data_revision = "native"
    native = keys()
    assert all(native[name] != scoped[name] for name in native)
