from lyra.sdk import PluginDefinition

from .metrics import (
    bounded_features,
    capacity,
    interval_width,
    reused_capacity,
    selection_size,
)


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        nodes=[
            capacity,
            reused_capacity,
            selection_size,
            bounded_features,
            interval_width,
        ]
    )


def create_capacity_plugin() -> PluginDefinition:
    return PluginDefinition(nodes=[capacity])
