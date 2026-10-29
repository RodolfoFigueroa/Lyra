"""Plugin definition for the minimal smoke-test example."""

from lyra.sdk import PluginDefinition

from smoke_plugin.graph import (
    city_population,
    city_source,
    population_baseline,
    selected_source,
)
from smoke_plugin.metrics import run_progress, run_summary, run_table


def create_plugin() -> PluginDefinition:
    """Create the example plugin with its table, graph, and progress examples.

    Returns:
        The complete smoke-test plugin definition.
    """
    return PluginDefinition(
        metrics=[
            run_table,
            run_summary,
            run_progress,
            city_source,
            city_population,
            selected_source,
            population_baseline,
        ]
    )
