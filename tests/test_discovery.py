"""Family projections preserve visibility and executable contracts."""

import pytest

from lyra.contracts.plugins import LocationInputDeclaration
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.discovery import Discovery
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract
from test_support.edition_catalog import discovery, edition_manifests
from test_support.plugin_graph import manifest


def test_direct_names_visibility_pagination_and_detachment() -> None:
    view = discovery()
    assert [item.name for item in view.search()] == ["measure_0", "measure_1"]
    assert [item.name for item in view.search(offset=1, limit=1)] == ["measure_1"]
    assert view.search("unavailable") == []
    assert view.search(limit=0) == []
    with pytest.raises(ValueError, match="nonnegative"):
        view.search(offset=-1)
    detail = view.inspect("measure_1")
    assert detail.node == "measure_1"
    assert detail.bindings["places"].edition == "1"
    assert detail.accepted_selections["places"] == [detail.bindings["places"]]
    detail.outputs.clear()
    view.search()[0].accepted_selections.clear()
    assert view.inspect("measure_1").outputs
    assert view.search()[0].accepted_selections
    assert view.fingerprint == discovery().fingerprint
    manifests = edition_manifests(2)
    assert manifests["benchmark"].nodes[1].kind == "metric"
    manifests["benchmark"].nodes[1].private = True
    view = Discovery(compile_catalog(manifests))
    assert [item.name for item in view.search()] == ["measure_0"]
    for name in ("unknown", "measure_1"):
        with pytest.raises(KeyError):
            view.inspect(name)


def test_constraints_and_source_definitions_survive_projection() -> None:
    manifests = edition_manifests(2)
    nodes = manifests["benchmark"].nodes
    for node in nodes:
        node.parameters_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 9, "default": 2}
            },
            "additionalProperties": False,
        }
    assert nodes[1].kind == "metric"
    native = nodes[1].location_inputs["places"].contract
    native.field_definitions = {native.family.structure.id_column: "Source identifier"}
    native.release = "Release A"
    view = Discovery(compile_catalog(manifests))
    detail = view.inspect("measure_1")
    assert nodes[1].kind == "metric"
    properties = detail.parameters["properties"]
    assert isinstance(properties, dict)
    count = properties["count"]
    assert isinstance(count, dict)
    assert count["maximum"] == 9
    assert detail.bindings["places"].field_definitions == native.field_definitions
    assert detail.accepted_selections["places"][0].release == "Release A"
    assert view.fingerprint != discovery().fingerprint


def test_routed_source_availability_is_edition_specific() -> None:
    manifests = edition_manifests(2)
    source = contract("regional")
    source.family = source.family.model_copy(update={"unit_type": "region"})
    operation = selector()
    operation.inputs.clear()
    operation.canonical = True
    operation.location_inputs = {"source": LocationInputDeclaration(contract=source)}
    operation.output_contract = (
        manifests["benchmark"].nodes[0].location_inputs["places"].contract
    )

    manifests["routes"] = manifest("routes", operation)
    view = Discovery(compile_catalog(manifests))
    assert [item.name for item in view.search("region")] == ["measure_0"]
    assert source in view.inspect("measure_0").accepted_selections["places"]
    assert source not in view.inspect("measure_1").accepted_selections["places"]
    assert manifests["benchmark"].nodes[0].kind == "metric"
    manifests["benchmark"].nodes[0].private = True
    assert Discovery(compile_catalog(manifests)).search("region") == []
