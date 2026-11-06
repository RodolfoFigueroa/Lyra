"""Reusable canonical operations fixtures."""

from lyra.contracts.plugins import (
    LocationOperationManifest,
    PluginInfo,
)
from test_support.location_operations import definition


def selector() -> LocationOperationManifest:
    result = (
        definition()
        .manifest(plugin=PluginInfo(name="test", version="1"), factory="test:create")
        .nodes[0]
    )
    assert result.kind == "location_operation"
    return result
