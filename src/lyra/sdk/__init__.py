"""Define metrics and location operations for Lyra plugins."""

from lyra.contracts.dependencies import Input
from lyra.contracts.origins import Origin
from lyra.contracts.resources import IOConfig, ResourceConfig, SecretReference
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.parquet import ParquetLocationProvider
from lyra.sdk.plugin import (
    NodeDescription,
    NodeImplementation,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.postgis import PostGISLocationProvider
from lyra.sdk.postgres import PostgresIO
from lyra.sdk.resources import BaseIO, Resource

__all__ = [
    "BaseIO",
    "Dependency",
    "IOConfig",
    "Input",
    "LocationDependency",
    "LocationOutputSchema",
    "LocationProvider",
    "MetricParameters",
    "NodeDescription",
    "NodeImplementation",
    "Origin",
    "ParquetLocationProvider",
    "PluginDefinition",
    "PostGISLocationProvider",
    "PostgresIO",
    "Resource",
    "ResourceConfig",
    "RunContext",
    "SecretReference",
    "location_operation",
    "metric",
]
