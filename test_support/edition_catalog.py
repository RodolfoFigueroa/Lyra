"""Catalog declarations for a measurement family with concrete editions."""

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    MetricManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.discovery import Discovery
from test_support.location_workloads import Provider


def edition_manifests(editions: int) -> dict[str, PluginManifest]:
    """Declare independent source bindings in edition order."""
    nodes = []
    for index in range(editions):
        native = Provider.contracts[0].model_copy(update={"edition": str(index)})
        nodes.append(
            MetricManifest(
                name=f"measure_{index}",
                family="measure",
                edition=str(index),
                description="Measurement",
                outputs={"value": OutputMetadata(columns=[])},
                location_inputs={"places": LocationInputDeclaration(contract=native)},
            )
        )
    return {
        "benchmark": PluginManifest(
            plugin=PluginInfo(name="benchmark", version="1"),
            factory="test_support.edition_catalog:edition_manifests",
            nodes=nodes,
        )
    }


def discovery(count: int = 2) -> Discovery:
    return Discovery(compile_catalog(edition_manifests(count)))
