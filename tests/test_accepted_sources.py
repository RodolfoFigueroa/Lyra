"""Concrete public source bindings compiled from enabled canonical routes."""

from typing import Any, cast

import pytest
from lyra.sdk.catalog import compile_catalog
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.models.plugin import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)

from tests.test_canonical_operations import selector
from tests.test_canonical_routes import contract
from tests.test_plugin_graph import manifest, node


def declarations() -> tuple[
    MetricManifest, LocationContract, LocationContract, list[LocationOperationManifest]
]:
    """Return two source editions reaching one native metric input."""
    native = contract("native")
    source = contract("source")
    source.family = source.family.model_copy(update={"unit_type": "state"})
    other = source.model_copy(update={"edition": "other"})
    metric = node("score", spatial={"places", "reference"})
    metric.location_inputs = {
        "places": LocationInputDeclaration(contract=native),
        "reference": LocationInputDeclaration(contract=other),
    }
    operations = []
    for index, binding in enumerate((source, other)):
        operation = selector()
        operation.name = f"select_{index}"
        operation.inputs.clear()
        operation.canonical = True
        operation.location_inputs = {
            "source": LocationInputDeclaration(contract=binding)
        }
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
    metric.location_inputs["places"].source_bindings = (source.reference,)
    manifests = {
        key: type(value).model_validate_json(value.model_dump_json())
        for key, value in manifests.items()
    }
    catalog = compile_catalog(manifests)
    places = catalog.accepted_sources["score"]["places"]
    assert places[source.family.collection, "state"].source == source.reference
    assert places[source.family.collection, "state"].operations == ("select_0",)
    native = metric.location_inputs["places"].contract.reference
    assert places[native.collection, native.unit_type].operations == ()
    reference = catalog.accepted_sources["score"]["reference"]
    assert next(iter(reference.values())).source == other.reference
    assert len(reference) == 1
    assert catalog.graph.nodes["score"].node.kind == "metric"
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


@pytest.mark.parametrize("kind", ["duplicate", "unreachable", "native"])
def test_invalid_bindings(kind: str) -> None:
    metric, source, _, operations = declarations()
    bindings = (source.reference,)
    name = "places"
    if kind == "duplicate":
        bindings *= 2
    elif kind == "unreachable":
        bindings = (source.reference.model_copy(update={"source": "missing"}),)
    else:
        operations[0].location_inputs["source"].contract = contract("alternative")
        bindings = (contract("alternative").reference,)
    for input_name, references in ({name: bindings}).items():
        metric.location_inputs[input_name].source_bindings = references
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
    metric.location_inputs["places"].source_bindings = (source.reference,)
    second = metric.model_copy(deep=True)
    second.name = "score_second"
    second.edition = "second"
    for declaration in second.location_inputs.values():
        declaration.source_bindings = ()
    second.location_inputs["places"].contract = metric.location_inputs[
        "places"
    ].contract.model_copy(update={"edition": "second"})
    compiled = compile_catalog(
        {
            "metrics": manifest("metrics", metric, second),
            "selectors": manifest("selectors", *operations),
        }
    )
    key = source.family.collection, source.family.unit_type
    assert key in compiled.accepted_sources["score"]["places"]
    assert key not in compiled.accepted_sources["score_second"]["places"]
