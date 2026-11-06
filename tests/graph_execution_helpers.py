"""Reusable graph execution fixtures."""

from collections.abc import Callable
from unittest.mock import Mock

import pandera.pandas as pa
from pydantic import Field, field_validator

from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition

TABLE = pa.DataFrameSchema({})


def executor(
    *handlers: Callable[..., object],
) -> tuple[GraphExecutor, BoundPlugin]:
    # Separate registrations exercise cross-plugin resolution without imports.
    plugins = {
        f"plugin_{index}": PluginDefinition(metrics=[handler])
        for index, handler in enumerate(handlers)
    }
    factory = Mock(side_effect=AssertionError("unexpected context creation"))
    bindings = {key: BoundPlugin(plugin) for key, plugin in plugins.items()}
    return GraphExecutor(
        LocalRunner.from_definitions(plugins).catalog, bindings, factory
    ), bindings["plugin_0"]


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
