"""Declarative operation scope across native results, manifests and caches."""

from unittest.mock import Mock, patch

import geopandas as gpd
import pytest
from lyra.sdk import (
    Input,
    Locations,
    PluginDefinition,
    PluginDefinitionError,
    content_fingerprints,
    geometry_shapely,
    location_operation,
)
from lyra.sdk.computation import computation_namespace
from lyra.sdk.graph import build_graph
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.plugin import NodeManifest
from pydantic import ValidationError

from tests.location_helpers import producer_result
from tests.test_location_schema import contract, declaration, locations


@pytest.mark.parametrize(
    "options",
    [
        {},
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

    with pytest.raises(PluginDefinitionError):
        location_operation(
            name="select",
            description="Select",
            output=contract(declaration()),
            **options,
        )(handler)


def definition(reference: str = "source") -> PluginDefinition:
    @location_operation(
        name="select",
        description="Select",
        output=contract(declaration()),
        scope_from=Input(reference),
    )
    def select(source: Locations, other: Locations) -> gpd.GeoDataFrame:
        assert source.ids == other.ids
        return producer_result(source, contract(declaration()))

    return PluginDefinition(location_operations=[select])


@pytest.mark.parametrize("empty", [False, True])
def test_inherited_scope_uses_named_effective_input_and_cached_scope(
    *,
    empty: bool,
) -> None:
    plugin = definition("other")
    source = locations() if empty else locations({})
    other = source.model_copy(
        update={"collection": "other:entities", "unit_type": "entity"}
    )
    invocation = plugin.prepare_resolved_invocation(
        "select", {"source": source, "other": other}
    )
    with (
        patch(
            "lyra.sdk.location_frames.ingest_geometries",
            wraps=geometry_shapely.ingest_geometries,
        ) as encode,
        patch.object(
            content_fingerprints, "compute", wraps=content_fingerprints.compute
        ) as fingerprint,
        patch.object(
            LocationSnapshot,
            "__init__",
            autospec=True,
            side_effect=LocationSnapshot.__init__,
        ) as snapshot,
    ):
        result = plugin.invoke_operation(invocation, {}, Mock())
    assert encode.call_count == fingerprint.call_count == snapshot.call_count == 1
    assert (result.collection, result.unit_type) == (other.collection, other.unit_type)
    assert result.ids == source.ids
    assert plugin.validate_cached_locations(invocation, result) == result
    with pytest.raises(ValueError, match="scope"):
        plugin.validate_cached_locations(invocation, source)


def test_manifest_scope_roundtrip_validation_and_cache_namespace() -> None:
    first, second = definition(), definition("other")
    manifest = first.node_manifests[0]
    assert NodeManifest.model_validate_json(manifest.model_dump_json()) == manifest
    encoded = manifest.model_dump(mode="json")
    encoded["location_output"]["scope"]["path"] = "missing"
    with pytest.raises(ValidationError, match="location input"):
        NodeManifest.model_validate(encoded)
    assert computation_namespace(
        build_graph({"plugin": first.node_manifests}), None, None
    ) != computation_namespace(
        build_graph({"plugin": second.node_manifests}), None, None
    )


def test_fixed_scope_direct_call_and_schema_reuse() -> None:
    schema = contract(declaration())
    frame = producer_result(locations({}), schema)

    @location_operation(
        name="make",
        description="Make",
        output=schema,
        collection="derived:units",
        unit_type="unit",
    )
    def make() -> gpd.GeoDataFrame:
        return frame

    assert make() is frame
    plugin = PluginDefinition(location_operations=[make])
    invocation = plugin.prepare_resolved_invocation("make", {})
    result = plugin.invoke_operation(invocation, {}, Mock())
    assert result.collection == "derived:units"
    assert result.unit_type == "unit"
    with pytest.raises(TypeError, match="GeoDataFrame"):
        plugin.normalize_location_result(invocation, result)
    assert "scope" not in schema.metadata.model_dump()
