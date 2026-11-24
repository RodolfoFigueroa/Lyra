"""Reusable graph arguments fixtures."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas import GeoDataFrame
from pydantic import Field, field_validator

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.locations import LocationContract
from lyra.runtime.arguments import PreparedResolvedInvocation, prepare_dependency
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.graph import ResolvedDependency
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import node_contract
from test_support.routing_plugins import provider

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

    def located(location: GeoDataFrame, bounds: GeoDataFrame) -> object:
        pytest.fail(f"Argument preparation executed a handler: {location}, {bounds}")

    handler: Callable[..., object] = located if spatial else independent
    if model is not None:
        handler = parameterized
        handler.__annotations__["parameters"] = model
    provider_id = location_provider_id(location_contract or node_contract())
    inputs = (
        {
            key: LocationDependency(provider=provider_id)
            for key in ("location", "bounds")
        }
        if spatial and model is None
        else {}
    )
    decorated = metric(
        code_revision="1",
        name=name,
        description=name,
        outputs={key: pa.DataFrameSchema(strict=True) for key in (name, "second")},
        inputs=inputs,
    )(handler)
    return BoundPlugin(
        PluginDefinition(
            metrics=[decorated],
            resources={provider_id: provider(provider_id)} if inputs else {},
        )
    )


def location_provider_id(contract: LocationContract) -> str:
    return contract.provider_scope.provider_id


def scoped_inputs(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: scoped_frame(value, node_contract())
        if isinstance(value, GeoDataFrame)
        else value
        for key, value in values.items()
    }


def scoped_frame(
    frame: GeoDataFrame, contract: LocationContract
) -> NativeLocationInput:
    name = location_provider_id(contract)
    registered = capture_provider(name, provider(name))
    frame = frame.assign(count=1)
    return NativeLocationInput(
        frame.rename(columns={frame.columns[0]: "location_id"}),
        registered.contract.reference,
        "location_id",
    )


def parameter_edge(
    owner: str,
    model: type[MetricParameters] | None,
    dependency: Dependency,
    target: type[MetricParameters] | None,
) -> ResolvedDependency:
    def calculate(table: pd.DataFrame, parameters: MetricParameters) -> pd.DataFrame:
        del parameters
        return table

    def independent(table: pd.DataFrame) -> pd.DataFrame:
        return table

    if model is not None:
        calculate.__annotations__["parameters"] = model
    handler = metric(
        name=owner,
        code_revision="1",
        description=owner,
        inputs={"table": dependency},
        outputs={owner: pa.DataFrameSchema(strict=True)},
    )(independent if model is None else calculate)
    catalog = compile_live_catalog(
        {
            "caller": PluginDefinition(metrics=[handler]),
            "source": plugin(target, name=dependency.producer).definition,
        }
    )
    return catalog.graph.dependencies[owner]["table"]


def location_edge(
    *, alias: bool = False, contract: LocationContract | None = None
) -> ResolvedDependency:
    provider_id = location_provider_id(contract or node_contract())

    def consume(
        location: GeoDataFrame,
        bounds: GeoDataFrame,
        alias: GeoDataFrame,
        table: pd.DataFrame,
    ) -> pd.DataFrame:
        del location, bounds, alias
        return table

    dependency = Dependency("producer", producer="producer")
    if alias:
        dependency = dependency.bind(location=Input("alias"))
    locations = {
        key: LocationDependency(provider=provider_id)
        for key in ("location", "bounds", "alias")
    }
    consumer = metric(
        name="consumer",
        description="Forward selected inputs",
        code_revision="1",
        inputs={**locations, "table": dependency},
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )(consume)
    catalog = compile_live_catalog(
        {
            "caller": PluginDefinition(metrics=[consumer]),
            "upstream": plugin(
                None, spatial=True, location_contract=contract
            ).definition,
        }
    )
    return catalog.graph.dependencies["consumer"]["table"]


def resolve(
    consumer: PreparedResolvedInvocation,
    model: type[MetricParameters] | None,
    overrides: dict[str, object] | None = None,
    *,
    output: str = "producer",
) -> PreparedResolvedInvocation:
    parameters = consumer.inspect_parameters()
    assert parameters is None or isinstance(parameters, MetricParameters)
    source_model = type(parameters) if parameters is not None else None
    edge = parameter_edge(
        "consumer",
        source_model,
        Dependency(output, producer="producer", parameters=overrides or {}),
        model,
    )
    return prepare_dependency(consumer, edge, plugin(model))


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


class YearSettings(MetricParameters):
    year: int = Field(default=2020, description="Year")


class NestedSettings(MetricParameters):
    settings: YearSettings = Field(description="Settings")
    history: list[YearSettings] = Field(description="History")
    named: dict[str, YearSettings] = Field(description="Named settings")
