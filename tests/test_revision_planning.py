"""Revision closures and structural namespaces remain independently scoped."""

from typing import Any, cast

import pytest
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.computation import computation_namespace
from lyra.sdk.dependencies import (
    Dependency,
    DependencyManifest,
    LocationDependencyManifest,
)

from tests.test_accepted_sources import declarations
from tests.test_plugin_graph import manifest, node


def test_selected_route_boundary_and_revision_only_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric, source, other, operations = declarations()
    metric.source_bindings = {"places": (source.reference,)}
    source.data_revision = "same"
    other.data_revision = "same"
    native = metric.location_contracts["places"]
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
    assert first.structural_namespace == second.structural_namespace
    assert first.revisions.nodes["score"] == second.revisions.nodes["score"]
    assert first.revisions.nodes["select_0"] != second.revisions.nodes["select_0"]
    assert first.revisions.contracts[source.reference].revision == "same"
    with pytest.raises(TypeError):
        cast("Any", first.revisions.contracts)[source.reference] = None
    assert computation_namespace(first.graph, None, "run") != first.structural_namespace
    monkeypatch.setattr("lyra.sdk.computation.installed_version", lambda _: "changed")
    assert compile_catalog(manifests).structural_namespace != first.structural_namespace


def test_transitive_dependencies_and_unversioned_target() -> None:
    _metric, source, _, operations = declarations()
    operation = operations[0]
    operation.canonical = False
    assert operation.output_contract is not None
    operation.output_contract.data_revision = None
    source.data_revision = "source"
    producer = node("producer", spatial={"places"})
    producer.location_contracts = {"places": source}
    middle = node(
        "middle", inputs={"data": Dependency("producer", producer="producer")}
    )
    root = node("root", inputs={"data": Dependency("middle", producer="middle")})
    for consumer in (middle, root):
        consumer.spatial_inputs = producer.spatial_inputs.copy()
        consumer.request_schema = producer.request_schema.copy()
        consumer.location_contracts = producer.location_contracts.copy()
    compiled = compile_catalog(
        {"test": manifest("test", producer, middle, root, operation)}
    )
    assert compiled.revisions.nodes["root"] == compiled.revisions.nodes["producer"]
    assert {b.revision for b in compiled.revisions.nodes[operation.name]} == {
        None,
        "source",
    }
    before = compiled.structural_namespace
    source.meaning = "Changed structural meaning"
    assert (
        compile_catalog(
            {"test": manifest("test", producer, middle, root, operation)}
        ).structural_namespace
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
    producer.location_contracts = {"places": operation.output_contract}
    root = node("root", spatial={"source"})
    root.location_contracts = {"source": source}
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
