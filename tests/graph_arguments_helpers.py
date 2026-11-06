"""Reusable graph arguments fixtures."""

from typing import TYPE_CHECKING, Any

import pandera.pandas as pa
import pytest
from pydantic import Field, field_validator

from lyra.contracts.locations import LocationContract
from lyra.data.locations import Locations
from lyra.runtime.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.runtime.invocation import BoundPlugin
from lyra.sdk.dependencies import Dependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import (
    node_contract,
)
from test_support.plugin_graph import graph, node

if TYPE_CHECKING:
    from collections.abc import Callable


class Consumer(MetricParameters):
    year: int | None = Field(default=2020, description="Year")
    baseline: int = Field(default=2010, description="Baseline year")
    unrelated: str = Field(default="ignored", description="Consumer only")


class Producer(MetricParameters):
    year: int = Field(default=2000, description="Year")
    count: int = Field(default=2, description="Producer default")


class Required(MetricParameters):
    year: int = Field(description="Year")


class Nullable(MetricParameters):
    year: int | None = Field(default=2000, description="Year")


class Objects(MetricParameters):
    settings: dict[str, int] = Field(default={"a": 1, "b": 2}, description="Settings")
    order: list[int] = Field(default=[1, 2], description="Ordered values")


class Normalized(MetricParameters):
    label: str = Field(description="Normalized label")

    @field_validator("label")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip().lower()


def plugin(
    model: type[MetricParameters] | None,
    *,
    name: str = "producer",
    spatial: bool = False,
    location_contract: LocationContract | None = None,
) -> BoundPlugin:
    def parameterized(parameters: MetricParameters) -> object:
        pytest.fail(f"Argument preparation executed a handler: {parameters}")

    def independent() -> object:
        pytest.fail("Argument preparation executed a handler")

    def located(location: Locations, bounds: Locations) -> object:
        pytest.fail(f"Argument preparation executed a handler: {location}, {bounds}")

    handler: Callable[..., object] = located if spatial else independent
    if model is not None:
        handler = parameterized
        handler.__annotations__["parameters"] = model
    return BoundPlugin(
        PluginDefinition(
            metrics=[
                metric(
                    name=name,
                    description=name,
                    outputs={name: pa.DataFrameSchema({})},
                    locations={
                        key: location_contract or node_contract()
                        for key in ("location", "bounds")
                    }
                    if spatial and model is None
                    else {},
                )(handler)
            ]
        )
    )


def resolve(
    consumer: PreparedResolvedInvocation,
    model: type[MetricParameters] | None,
    overrides: dict[str, object] | None = None,
    *,
    output: str = "producer",
) -> PreparedResolvedInvocation:
    assert consumer.parameters is None or isinstance(
        consumer.parameters, MetricParameters
    )
    source_model = (
        type(consumer.parameters) if consumer.parameters is not None else None
    )
    registry = graph(
        node(
            "consumer",
            parameters=source_model,
            inputs={
                "table": Dependency(
                    output, parameters=overrides or {}, producer="producer"
                )
            },
        ),
        node("producer", parameters=model, outputs=("producer", "second")),
    )
    return prepare_dependency(
        consumer, registry.dependencies["consumer"]["table"], plugin(model)
    )


def invocation(
    model: type[MetricParameters], values: dict[str, object]
) -> PreparedResolvedInvocation:
    return plugin(model, name="consumer").prepare_resolved_invocation(
        "consumer", {"parameters": values}
    )


def geometry() -> dict[str, Any]:
    return {
        "collection": "test:units",
        "unit_type": "lot",
        "crs": {"type": "name", "properties": {"name": "EPSG:6372"}},
        "features": [
            {
                "id": "area",
                "type": "Feature",
                "properties": {"label": "native"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                },
            }
        ],
    }


class TextYear(MetricParameters):
    year: str = Field(default="invalid", description="Year label")


class ExcludedYear(MetricParameters):
    year: int = Field(default=2020, description="Year", exclude=True)


class NestedExcluded(MetricParameters):
    settings: ExcludedYear = Field(description="Settings")
    history: list[ExcludedYear] = Field(description="History")
    named: dict[str, ExcludedYear] = Field(description="Named settings")
