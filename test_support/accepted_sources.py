"""Reusable accepted sources fixtures."""

from typing import Any

from lyra.contracts.locations import LocationContract
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
)
from lyra.runtime.planning.catalog import CompiledCatalog
from test_support.canonical_operations import selector
from test_support.canonical_routes import contract
from test_support.location_operations import Parameters
from test_support.manifest_catalog import compile_catalog
from test_support.parameter_catalog import with_models
from test_support.plugin_graph import manifest, node


def declarations() -> tuple[
    MetricManifest, LocationContract, LocationContract, list[LocationOperationManifest]
]:
    """Return two source providers reaching one native metric input."""
    native = contract("native")
    source = contract("source")
    other = contract("other")
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
    catalog = with_models(catalog, {op.name: Parameters for op in operations})
    request = {
        "metric": "score",
        "inputs": {
            name: {
                "provider": contract.family.collection,
                "ids": ["unknown"],
            }
            for name, contract in (("places", source), ("reference", other))
        },
    }
    return catalog, request
