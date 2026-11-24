"""Attempt identity and selected-use topology remain independent of scheduling."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from lyra.contracts.references import validate_job_id
from lyra.runtime.execution import GraphExecutor, RunState
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.report_collection import ReportCollection
from tests.bound_invocation_helpers import bound
from tests.dependency_route_helpers import definitions, request


def test_shared_operations_and_distinct_parameter_uses() -> None:
    _, _, root = bound(["a", "b"])
    report = ReportCollection("a" * 32)
    report.index(root)
    first, second = root.dependencies.values()
    assert report.root_use_id == report.use_id(root)
    assert report.use_id(first) != report.use_id(second)
    first_input = first.locations["places"]
    second_input = second.locations["places"]
    assert report.location_use_id(first_input) == report.location_use_id(second_input)
    links = dict(report.uses[report.root_use_id].inputs)
    assert links["first"] == report.use_id(first)
    assert links["second"] == report.use_id(second)
    operation = report.uses[report.location_use_id(first_input)]
    assert operation.kind == "operation"
    assert operation.target_use_id is not None
    assert report.uses[operation.target_use_id].node == "counties"
    assert len([use for use in report.uses.values() if use.node == "select"]) == 1


def test_replacements_and_partial_demands_keep_original_attribution() -> None:
    _, _, root = bound(["a", "b"])
    report = ReportCollection("a" * 32)
    report.index(root)
    producer = root.dependencies["first"]
    symbolic = producer.locations["places"]
    resolved = replace(symbolic, ids=("x", "y"), pairs=(("a", "x"), ("b", "y")))
    work = replace(producer, locations={"places": resolved}, primary_input="places")
    partial = work.demand(frozenset({"x"}))
    assert report.use_id(partial.invocation) == report.use_id(producer)
    assert report.location_use_id(resolved.select(frozenset({"x"}))) == (
        report.location_use_id(symbolic)
    )
    assert symbolic.ids is None
    assert root.locations["seed"].ids == ("a", "b")


def test_routes_index_only_selected_operations_without_acquisition() -> None:
    catalog = compile_live_catalog(definitions(binding="matching"))
    plan = prepare_execution(catalog, request())
    root = bind_execution(catalog, plan)
    report = ReportCollection("a" * 32)
    report.index(root)
    operations = {use.node for use in report.uses.values() if use.kind == "operation"}
    assert operations == {"first", "finish", "reference_first"}
    left = root.dependencies["left"]
    finish = report.uses[report.location_use_id(left.locations["places"])]
    assert finish.node == "finish"
    first = report.uses[dict(finish.inputs)["source"]]
    assert first.node == "first"
    assert report.uses[dict(first.inputs)["source"]].kind == "provider"
    targets = [
        use.target_use_id for use in report.uses.values() if use.node == "finish"
    ]
    assert len(set(targets)) == 2


def test_empty_attempt_indexes_bypassed_dependencies_and_releases_bindings() -> None:
    catalog, plan, _ = bound([])
    executor = GraphExecutor(catalog, Mock())
    state = RunState()
    state.bound_root = executor.validate_plan(plan)
    state.report.index(state.bound_root)
    report = state.report
    assert validate_job_id(report.job_id) == report.job_id
    assert {use.node for use in report.uses.values()} == {
        "consumer",
        "producer",
        "select",
        "tracts",
        "counties",
    }
    root = state.bound_root
    location = root.locations["seed"]
    state.clear()
    assert state.bound_root is None
    with pytest.raises(KeyError):
        report.use_id(root)
    with pytest.raises(KeyError):
        report.location_use_id(location)
    assert report.root_use_id in report.uses


def test_equivalent_metric_bindings_share_one_producer() -> None:
    _, _, root = bound(["a", "b"])
    producer = root.dependencies["first"]
    equivalent = replace(producer, report_token=object(), parameter_use_key="elsewhere")
    root = replace(root, dependencies={"first": producer, "second": equivalent})
    report = ReportCollection("a" * 32)
    report.index(root)
    assert report.use_id(producer) == report.use_id(equivalent)
    assert report.root_use_id is not None
    links = dict(report.uses[report.root_use_id].inputs)
    assert links["first"] == links["second"]


@pytest.mark.parametrize("ids", [[], ["a", "b"]])
def test_captured_graph_describes_named_outputs_and_direct_origins(
    ids: list[str],
) -> None:
    catalog, plan, root = bound(ids)
    report = ReportCollection("a" * 32)
    report.capture(root, catalog, plan)
    description = report.descriptions[report.use_id(root)]
    assert report.request == plan.request
    assert report.build_revision is None
    assert description.parameters == {"year": 2025}
    assert description.primary_input is None
    assert description.origins == []
    assert description.outputs["result"].scope == "unpartitioned"
    assert description.outputs["result"].owner_scope is None
    producer = report.descriptions[description.inputs["first"].source_use_id]
    assert description.inputs["first"].output_name == "value"
    assert description.inputs["first"].provider_scope is None
    assert producer.parameters == {"year": 2020}
    assert [(o.id, o.revision) for o in producer.origins] == [("census", "2025")]
    assert producer.code_revision == "1"
    operation = report.descriptions[producer.inputs["places"].source_use_id]
    assert operation.primary_input == "source"
    assert operation.origins == []
    assert operation.operation is not None
    assert operation.operation.target_adapter is None
    assert operation.operation.target_scope.provider_id == "counties"
    assert producer.inputs["places"].provider_scope == operation.operation.target_scope
    source = operation.inputs["source"].provider_scope
    assert source is not None
    assert source.provider_id == "tracts"
    assert source.origins == operation.operation.target_scope.origins
    assert source != operation.operation.target_scope
    assert set(report.descriptions) == set(report.uses)
    plan.request.inputs["seed"].ids.append("changed")
    plan.request.parameters["year"] = 1900
    node = catalog.graph.nodes["consumer"].node
    assert node.kind == "metric"
    node.outputs["result"].columns.clear()
    assert report.request is not None
    assert report.request.inputs["seed"].ids == ids
    assert report.request.parameters == {"year": 2025}
    assert description.parameters == {"year": 2025}


def test_routed_request_preserves_source_selections() -> None:
    catalog = compile_live_catalog(
        definitions(binding="matching"), build_revision="build-3"
    )
    plan = prepare_execution(catalog, request())
    root = bind_execution(catalog, plan)
    report = ReportCollection("a" * 32)
    report.capture(root, catalog, plan)
    assert report.request == plan.request
    assert report.build_revision == "build-3"
    assert {use.node for use in report.uses.values() if use.kind == "operation"} == {
        "first",
        "finish",
        "reference_first",
    }
    for use_id, use in report.uses.items():
        if use.kind == "operation":
            description = report.descriptions[use_id]
            assert description.primary_input == "source"
            assert set(description.inputs) == {"source"}
