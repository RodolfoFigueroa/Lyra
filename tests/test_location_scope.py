"""Declarative operation scope across native results, manifests and caches."""

from unittest.mock import Mock, patch

import geopandas as gpd
import pytest
from pydantic import ValidationError

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.plugins import NODE_MANIFEST_ADAPTER
from lyra.data import content_fingerprints, geometry_shapely
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.locations import Locations
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.computation import compile_computation
from lyra.runtime.planning.graph import build_graph
from lyra.sdk.plugin import PluginDefinition, location_operation
from test_support.location_helpers import (
    fixture_locations,
    node_contract,
    producer_result,
)
from test_support.location_schema import contract, declaration, locations


@pytest.mark.parametrize(
    "options",
    [
        {"collection": "test:units"},
        {"unit_type": "point"},
        {"collection": "invalid", "unit_type": "point"},
        {"collection": "test:units", "unit_type": " "},
        {"scope_from": Input("missing")},
        {"scope_from": Input("parameters.year")},
        {"scope_from": Input("source"), "collection": "test:units"},
        {"scope_from": "source"},
        {"scope_from": None},
    ],
)
def test_invalid_scope_declarations(options: dict) -> None:
    def handler(source: Locations) -> gpd.GeoDataFrame:
        return producer_result(source, contract(declaration()))

    with pytest.raises((PluginDefinitionError, TypeError)):
        location_operation(
            locations={"source": node_contract()},
            output_contract=node_contract("test:units", "lot", contract(declaration())),
            name="select",
            description="Select",
            output=contract(declaration()),
            **options,
        )(handler)


def definition(reference: str = "source") -> PluginDefinition:
    @location_operation(
        locations={
            "source": node_contract("example:places", "point", contract(declaration())),
            "other": node_contract("other:entities", "entity", contract(declaration())),
        },
        output_contract=node_contract(
            "other:entities" if reference == "other" else "example:places",
            "entity" if reference == "other" else "point",
            contract(declaration()),
        ),
        name="select",
        description="Select",
        output=contract(declaration()),
    )
    def select(source: Locations, other: Locations) -> gpd.GeoDataFrame:
        assert source.ids == other.ids
        return producer_result(source, contract(declaration()))

    return PluginDefinition(location_operations=[select])


@pytest.mark.parametrize("empty", [False, True])
def test_concrete_output_scope_and_cached_scope(
    *,
    empty: bool,
) -> None:
    binding = BoundPlugin(definition("other"))
    source = locations() if empty else locations({})
    other = fixture_locations(
        source.to_dict() | {"collection": "other:entities", "unit_type": "entity"}
    )
    invocation = binding.prepare_resolved_invocation(
        "select", {"source": source, "other": other}
    )
    with (
        patch(
            "lyra.data.geometry_shapely.ingest_geometries",
            wraps=geometry_shapely.ingest_geometries,
        ) as encode,
        patch.object(
            content_fingerprints,
            "typed_snapshot",
            wraps=content_fingerprints.typed_snapshot,
        ) as fingerprint,
        patch.object(
            LocationSnapshot,
            "__init__",
            autospec=True,
            side_effect=LocationSnapshot.__init__,
        ) as snapshot,
    ):
        result = binding.invoke_operation(invocation, {}, Mock())
    assert encode.call_count == snapshot.call_count == 1
    assert fingerprint.call_count == 0
    assert (result.collection, result.unit_type) == (other.collection, other.unit_type)
    assert result.ids == source.ids
    assert binding.validate_cached_locations(invocation, result) == result
    with pytest.raises(ValueError, match="scope"):
        binding.validate_cached_locations(invocation, source)


def test_manifest_scope_roundtrip_validation_and_cache_namespace() -> None:
    first, second = definition(), definition("other")
    manifest = first.node_manifests[0]
    assert NODE_MANIFEST_ADAPTER.validate_json(manifest.model_dump_json()) == manifest
    encoded = manifest.model_dump(mode="json")
    encoded["output_contract"]["family"]["collection"] = "invalid collection"
    with pytest.raises(ValidationError):
        NODE_MANIFEST_ADAPTER.validate_python(encoded)
    assert compile_computation(
        build_graph({"plugin": first.node_manifests}), None
    ) != compile_computation(build_graph({"plugin": second.node_manifests}), None)


def test_fixed_scope_direct_call_and_schema_reuse() -> None:
    schema = contract(declaration())
    frame = producer_result(locations({}), schema)

    @location_operation(
        output_contract=node_contract("derived:units", "unit", schema),
        name="make",
        description="Make",
        output=schema,
    )
    def make() -> gpd.GeoDataFrame:
        return frame

    assert make() is frame
    binding = BoundPlugin(PluginDefinition(location_operations=[make]))
    invocation = binding.prepare_resolved_invocation("make", {})
    result = binding.invoke_operation(invocation, {}, Mock())
    assert result.collection == "derived:units"
    assert result.unit_type == "unit"
    with pytest.raises(TypeError, match="GeoDataFrame"):
        binding.normalize_location_result(invocation, result)
    assert "scope" not in schema.metadata.model_dump()
