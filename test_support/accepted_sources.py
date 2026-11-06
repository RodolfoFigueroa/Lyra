"""Reusable accepted sources fixtures."""

from typing import Any

from lyra.contracts.locations import LocationContract
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)
from lyra.runtime.planning.catalog import CompiledCatalog, compile_catalog
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract
from test_support.plugin_graph import manifest, node


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


def routed_request() -> tuple[CompiledCatalog, dict[str, Any]]:
    """A routed two-input request with unresolved IDs."""
    metric, source, other, operations = declarations()
    metric.location_inputs["places"].source_bindings = (source.reference,)
    catalog = compile_catalog({"test": manifest("test", metric, *operations)})
    request = {
        "metric": "score",
        "inputs": {
            name: {
                "collection": contract.family.collection,
                "unit_type": contract.family.unit_type,
                "ids": ["unknown"],
            }
            for name, contract in (("places", source), ("reference", other))
        },
    }
    return catalog, request
