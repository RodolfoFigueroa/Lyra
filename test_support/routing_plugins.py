"""SDK declarations for acquisition-free routing workloads."""

from collections.abc import Callable

import geopandas as gpd
import pandas as pd

from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_operations import Parameters, Places
from test_support.table_fixtures import value_frame, value_schema


def provider(name: str) -> type[Places]:
    """Build an in-process provider with a distinct dataset identity."""

    class Provider(Places):
        id = name

    return Provider


def conversion(name: str, source: str, target: str) -> Callable:
    """Declare a canonical conversion with a real defaulted parameter."""

    @location_operation(
        name=name,
        code_revision="1",
        description="Convert records.",
        inputs={"source": LocationDependency(provider=source)},
        target_provider=target,
        canonical=True,
    )
    def select(
        source: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in source.location_id.iloc[: parameters.count]}

    return select


def score_plugin(
    model: type[MetricParameters] | None = None,
    *,
    private: bool = False,
    target: str = "native",
    name: str = "score",
) -> PluginDefinition:
    """Declare a public or private metric with two independent location inputs."""

    def score(places: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        del places, reference
        return value_frame()

    def parameterized(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        *,
        parameters: MetricParameters,
    ) -> pd.DataFrame:
        del places, reference, parameters
        return value_frame()

    handler: Callable = score
    if model is not None:
        parameterized.__annotations__["parameters"] = model
        handler = parameterized
    decorated = metric(
        name=name,
        code_revision="1",
        description="Score places.",
        private=private,
        outputs={"result": value_schema()},
        inputs={
            "places": LocationDependency(provider=target),
            "reference": LocationDependency(provider="other"),
        },
    )(handler)
    return PluginDefinition(metrics=[decorated])


def routed_plugins(
    model: type[MetricParameters] | None = None,
    *,
    private: bool = False,
    multihop: bool = False,
) -> dict[str, PluginDefinition]:
    """Register two sources reaching the native input."""
    names = ["native", "source", "other"]
    operations = [conversion("select_1", "other", "native")]
    if multihop:
        names.append("middle")
        operations.extend(
            [
                conversion("select_0", "source", "middle"),
                conversion("second", "middle", "native"),
            ]
        )
    else:
        operations.append(conversion("select_0", "source", "native"))
    return {
        "metrics": score_plugin(model, private=private),
        "routes": PluginDefinition(
            resources={name: provider(name) for name in names},
            location_operations=operations,
        ),
    }
