"""Reusable graph execution fixtures."""

from collections.abc import Callable
from unittest.mock import Mock

from pydantic import Field, field_validator

from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.sdk.locations import LocationProvider
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition
from test_support.table_fixtures import value_schema

TABLE = value_schema()


def executor(
    *handlers: Callable[..., object],
    resources: dict[str, type[LocationProvider]] | None = None,
) -> tuple[GraphExecutor, BoundPlugin]:
    # Separate registrations exercise cross-plugin resolution without imports.
    plugins = {
        f"plugin_{index}": PluginDefinition(metrics=[handler])
        for index, handler in enumerate(handlers)
    }
    factory = Mock(side_effect=AssertionError("unexpected context creation"))
    if resources:
        plugins["locations"] = PluginDefinition(resources=resources)
    catalog = LocalRunner.from_definitions(plugins).catalog
    return GraphExecutor(catalog, factory), catalog.implementations["plugin_0"]


class Label(MetricParameters):
    label: str = Field(default="a", description="Label")

    @field_validator("label")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip().lower()


class Consumer(Label):
    extra: int = Field(default=1, description="Consumer only")


class Year(MetricParameters):
    year: int = Field(default=2020, description="Year")
