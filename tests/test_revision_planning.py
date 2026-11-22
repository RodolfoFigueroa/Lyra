"""Revision closures for registered provider and operation uses."""

from typing import Any, cast

import pytest

from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from test_support.accepted_sources import routed_request
from tests.dependency_route_helpers import definitions, request


def test_selected_route_revision_boundary() -> None:
    catalog, inputs = routed_request()
    plan = prepare_execution(catalog, inputs)
    assert {item.contract.collection for item in plan.revisions.calculation} == {
        "native",
        "other",
    }
    assert {item.contract.collection for item in plan.revisions.selection} == {
        "native",
        "source",
        "other",
    }
    assert len(plan.revisions.bindings) == 3
    with pytest.raises(TypeError):
        cast("Any", catalog.revisions.contracts)[plan.inputs[0].binding.source] = None


def test_transitive_provider_scope_changes() -> None:
    before = compile_live_catalog(definitions(binding="nested"))
    after = compile_live_catalog(
        definitions({"middle_provider": "changed"}, binding="nested")
    )
    plan = prepare_execution(before, request())
    changed = prepare_execution(after, request())
    assert plan.revisions.calculation != changed.revisions.calculation
    assert plan.computation_identity != changed.computation_identity
    assert before.revisions.nodes["producer"] == after.revisions.nodes["producer"]
    assert before.revisions.nodes["explicit"] != after.revisions.nodes["explicit"]
