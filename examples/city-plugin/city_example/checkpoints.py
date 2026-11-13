"""Independently runnable registration checkpoints for the tutorial."""

import geopandas as gpd
import pandas as pd

from city_example.calculations import calculate_lengths, calculate_maintenance
from city_example.data import load_city, street_frame
from city_example.metrics import (
    MaintenanceParameters,
    street_conditions,
    street_lengths,
    street_maintenance,
)
from city_example.plugin import build_plugin, create_plugin
from city_example.provider import CITY_ORIGIN, CityStreetProvider
from city_example.schemas import LENGTHS, MAINTENANCE, STREETS
from lyra.sdk import LocationDependency, PluginDefinition, metric


# docs:start first
@metric(
    code_revision="1",
    name="city_street_lengths",
    description="Centreline lengths for all three streets in the bundled city.",
    origins=(CITY_ORIGIN,),
    outputs={"lengths": LENGTHS},
)
def fixture_lengths() -> pd.DataFrame:
    """Expose the existing calculation over a fixed fixture.

    Returns:
        Three identified lengths, measured in projected metres.
    """
    return calculate_lengths(street_frame(load_city()))


def first_plugin() -> PluginDefinition:
    """Return the first calculation without location selection."""
    return PluginDefinition(metrics=[fixture_lengths])


# docs:end first


# docs:start selected
def selected_plugin() -> PluginDefinition:
    """Return street selection without the later maintenance graph."""
    return PluginDefinition(
        metrics=[street_lengths],
        resources={"city_streets": CityStreetProvider},
    )


# docs:end selected


# docs:start direct
@metric(
    code_revision="1",
    name="city_street_maintenance",
    description="Street maintenance workload under a chosen condition threshold.",
    inputs={"streets": LocationDependency(provider=STREETS)},
    outputs={"maintenance": MAINTENANCE},
)
def direct_maintenance(
    streets: gpd.GeoDataFrame, parameters: MaintenanceParameters
) -> pd.DataFrame:
    """Adapt the calculation before introducing explicit graph dependencies.

    Returns:
        One maintenance decision per selected street.
    """
    conditions = pd.DataFrame(streets.drop(columns="geometry")).rename(
        columns={"location_id": "street_id"}
    )
    return calculate_maintenance(
        calculate_lengths(streets),
        conditions,
        parameters.condition_threshold,
    )


# docs:end direct


def parameters_plugin() -> PluginDefinition:
    """Return the parameterized calculation with its own provider."""
    return PluginDefinition(
        metrics=[street_lengths, direct_maintenance],
        resources={"city_streets": CityStreetProvider},
    )


def dependencies_plugin() -> PluginDefinition:
    """Return only the street table graph for the dependency chapter."""
    return PluginDefinition(
        metrics=[street_lengths, street_conditions, street_maintenance],
        resources={"city_streets": CityStreetProvider},
    )


def checkpoint(chapter: int) -> PluginDefinition:
    """Return exactly the definitions introduced through a chapter.

    Returns:
        An independent plugin definition, including its required provider.

    Raises:
        ValueError: If the chapter is outside the runnable sequence.
    """
    factories = {
        1: first_plugin,
        2: selected_plugin,
        3: parameters_plugin,
        4: dependencies_plugin,
    }
    if chapter in factories:
        return factories[chapter]()
    if chapter == 5:
        return build_plugin(canonical=False)
    if chapter in {6, 7}:
        return create_plugin()
    message = "Runnable chapters are numbered 1 through 7."
    raise ValueError(message)
