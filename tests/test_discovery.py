"""Family projections preserve visibility and executable contracts."""

import pytest

from lyra.contracts.plugins import LocationInputDeclaration
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.discovery import Discovery
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract
from test_support.edition_catalog import discovery, edition_manifests
from test_support.plugin_graph import manifest


def test_named_family_is_compact_and_requires_selection() -> None:
    view = discovery()
    (summary,) = view.search()
    assert summary.requires_edition
    assert len(summary.editions) == 2
    assert "outputs" not in summary.model_dump()
    shared = view.inspect("measure")
    assert shared.node is None
    assert shared.bindings == shared.accepted_selections == {}
    concrete = view.inspect("measure", edition="1")
    assert concrete.node == "measure_1"
    assert concrete.bindings["places"].edition == "1"
    assert concrete.accepted_selections["places"] == [concrete.bindings["places"]]
    assert concrete.outputs == shared.outputs
    assert len(discovery(1).inspect("measure").family.editions) == 1
    assert discovery(1).inspect("measure").node is None


def test_private_variants_and_implicit_contract() -> None:
    manifests = edition_manifests(2)
    nodes = manifests["benchmark"].nodes
    assert nodes[1].kind == "metric"
    nodes[1].private = True
    view = Discovery(compile_catalog(manifests))
    assert [choice.edition for choice in view.search()[0].editions] == ["0"]
    with pytest.raises(KeyError):
        view.inspect("measure", edition="1")
    assert nodes[0].kind == "metric"
    nodes[0].private = True
    assert Discovery(compile_catalog(manifests)).search() == []
    nodes.pop()
    assert nodes[0].kind == "metric"
    nodes[0].private = False
    assert nodes[0].kind == "metric"
    nodes[0].edition = None
    view = Discovery(compile_catalog(manifests))
    assert not view.search()[0].requires_edition
    assert view.inspect("measure").node == "measure_0"


def test_search_pagination_fingerprint_and_detachment() -> None:
    view = discovery()
    source = view.inspect("measure", edition="0").bindings["places"]
    assert len(view.search(source.family.unit_type)) == 1
    assert [choice.edition for choice in view.search("1")[0].editions] == ["1"]
    assert view.search("unavailable") == []
    assert view.search(offset=1) == []
    assert view.search(limit=0) == []
    with pytest.raises(ValueError, match="nonnegative"):
        view.search(offset=-1)
    view.search()[0].editions.clear()
    assert len(view.search()[0].editions) == 2
    assert view.fingerprint == discovery().fingerprint


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
    nodes[1].edition_metadata = {"methodology": "Release-specific weights"}
    native = nodes[1].location_inputs["places"].contract
    native.field_definitions = {native.family.structure.id_column: "Source identifier"}
    native.release = "Release A"
    view = Discovery(compile_catalog(manifests))
    detail = view.inspect("measure", edition="1")
    assert nodes[1].kind == "metric"
    assert detail.edition_metadata == nodes[1].edition_metadata
    assert view.inspect("measure").edition_metadata == {}
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
    assert [choice.edition for choice in view.search("region")[0].editions] == ["0"]
    assert source in view.inspect("measure", edition="0").accepted_selections["places"]
    assert (
        source not in view.inspect("measure", edition="1").accepted_selections["places"]
    )
    assert manifests["benchmark"].nodes[0].kind == "metric"
    manifests["benchmark"].nodes[0].private = True
    assert Discovery(compile_catalog(manifests)).search("region") == []
