from lyra.sdk import PluginDefinition
from test_support.location_resolution import Provider

from .metrics import (
    bounded_features,
    capacity,
    interval_width,
    reused_capacity,
    selection_size,
)


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[
            capacity,
            reused_capacity,
            selection_size,
            bounded_features,
            interval_width,
        ],
        resources={"locations": Provider},
    )


def create_capacity_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[capacity], resources={"locations": Provider})
