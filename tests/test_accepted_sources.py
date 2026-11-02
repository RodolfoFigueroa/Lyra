"""Concrete public source bindings compiled from enabled canonical routes."""

from typing import Any, cast

import pytest
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.models.plugin import NodeManifest

from tests.test_canonical_operations import selector
from tests.test_canonical_routes import contract
from tests.test_plugin_graph import manifest, node


def declarations() -> tuple[
    NodeManifest, LocationContract, LocationContract, list[NodeManifest]
]:
    """Return two source editions reaching one native metric input."""
    native = contract("native")
    source = contract("source")
    source.family = source.family.model_copy(update={"unit_type": "state"})
    other = source.model_copy(update={"edition": "other"})
    metric = node("score", spatial={"places", "reference"})
    metric.location_contracts = {"places": native, "reference": other}
    operations = []
    for index, binding in enumerate((source, other)):
        operation = selector()
        operation.name = f"select_{index}"
        operation.inputs.clear()
        operation.canonical = True
        operation.location_contracts = {"source": binding}
        operation.output_contract = native
        operations.append(operation)
    return metric, source, other, operations


def test_ambiguity_binding_roundtrip_and_multiple_inputs() -> None:
    metric, source, other, operations = declarations()
    manifests = {
        "metric": manifest("metric", metric),
        "routes": manifest("routes", *operations),
    }
    with pytest.raises(ValueError, match="Ambiguous source binding"):
        compile_catalog(manifests)
    metric.source_bindings = {"places": (source.reference,)}
    manifests = {
        key: type(value).model_validate_json(value.model_dump_json())
        for key, value in manifests.items()
    }
    catalog = compile_catalog(manifests)
    places = catalog.accepted_sources["score"]["places"]
    assert places[source.family.collection, "state"].source == source.reference
    assert places[source.family.collection, "state"].operations == ("select_0",)
    native = metric.location_contracts["places"].reference
    assert places[native.collection, native.unit_type].operations == ()
    reference = catalog.accepted_sources["score"]["reference"]
    assert next(iter(reference.values())).source == other.reference
    assert len(reference) == 1
    assert catalog.graph.nodes["score"].node.outputs == metric.outputs
    assert catalog.graph.nodes["select_0"].node.description == operations[0].description
    with pytest.raises(TypeError):
        cast("Any", places)["invalid", "invalid"] = next(iter(places.values()))


def test_native_only_and_private() -> None:
    metric, _, _, _ = declarations()
    compiled = compile_catalog({"metric": manifest("metric", metric)})
    assert all(
        len(sources) == 1 for sources in compiled.accepted_sources["score"].values()
    )
    metric.private = True
    compiled = compile_catalog({"metric": manifest("metric", metric)})
    assert "score" in compiled.graph.nodes
    assert "score" not in compiled.accepted_sources


@pytest.mark.parametrize("kind", ["unknown", "duplicate", "unreachable", "native"])
def test_invalid_bindings(kind: str) -> None:
    metric, source, _, operations = declarations()
    bindings = (source.reference,)
    name = "places"
    if kind == "unknown":
        name = "missing"
    elif kind == "duplicate":
        bindings *= 2
    elif kind == "unreachable":
        bindings = (source.reference.model_copy(update={"source": "missing"}),)
    else:
        operations[0].location_contracts["source"] = contract("alternative")
        bindings = (contract("alternative").reference,)
    metric.source_bindings = {name: bindings}
    with pytest.raises(ValueError, match="binding"):
        compile_catalog(
            {
                "metric": manifest("metric", metric),
                "routes": manifest("routes", *operations),
            }
        )


def test_edition_specific_reachability() -> None:
    metric, source, _, operations = declarations()
    metric.family = "measurement"
    metric.edition = "first"
    metric.source_bindings = {"places": (source.reference,)}
    second = metric.model_copy(deep=True)
    second.name = "score_second"
    second.edition = "second"
    second.source_bindings = {}
    second.location_contracts["places"] = metric.location_contracts[
        "places"
    ].model_copy(update={"edition": "second"})
    compiled = compile_catalog(
        {
            "metrics": manifest("metrics", metric, second),
            "selectors": manifest("selectors", *operations),
        }
    )
    key = source.family.collection, source.family.unit_type
    assert key in compiled.accepted_sources["score"]["places"]
    assert key not in compiled.accepted_sources["score_second"]["places"]
