"""Live, acquisition-free declarations for receiving-route acceptance."""

from collections.abc import Callable

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from tests.node_declaration_helpers import provider


class Options(MetricParameters):
    count: int = Field(2, gt=0, description="Count")
    tags: list[str] = Field(default_factory=list, description="Tags")


def definitions(
    changes: dict[str, str] | None = None,
    *,
    binding: str = "explicit",
    options: type[MetricParameters] = Options,
    origins: tuple[Origin, ...] | None = None,
) -> dict[str, PluginDefinition]:
    changes = changes or {}
    description = changes.get("description", "Plan a receiving use")
    middle = changes.get("middle", "middle")

    def operation(
        name: str, source: str, target: str, *, canonical: bool = True
    ) -> Callable:
        def select(source: gpd.GeoDataFrame, *, parameters: MetricParameters) -> dict:
            del source, parameters
            pytest.fail("Planning invoked an operation")

        select.__annotations__["parameters"] = options
        return location_operation(
            name=name,
            code_revision=changes.get(name, "1"),
            description=description,
            canonical=canonical,
            inputs={"source": LocationDependency(provider=source)},
            target_provider=target,
        )(select)

    first = operation("first", "source", middle)
    reference_first = operation("reference_first", "reference", middle)
    alternate = operation("alternate", "source", middle)
    finish = operation("finish", middle, "target")
    initial = operation("initial", "source", middle, canonical=False)
    explicit = operation("explicit", "target", middle, canonical=False)
    unused = operation("unused", "reference", "reference", canonical=False)
    table = pa.DataFrameSchema(strict=True)

    @metric(
        name="producer",
        code_revision="1",
        description=description,
        private=True,
        inputs={
            "places": LocationDependency(provider="target"),
            "reference": LocationDependency(provider="reference"),
        },
        outputs={"value": table},
    )
    def producer(
        places: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, *, parameters: Options
    ) -> pd.DataFrame:
        del places, reference, parameters
        pytest.fail("Planning invoked a producer")

    dependency = Dependency("value", producer="producer", parameters={"count": 5})
    if binding == "explicit":
        dependency = dependency.bind(places=Input("places"))
    elif binding == "nested":
        dependency = dependency.bind(
            places=LocationDependency("explicit", parameters={"count": 7}).bind(
                source=LocationDependency(
                    "initial", parameters={"count": Input("parameters.count")}
                ).bind(source=Input("places"))
            )
        )

    @metric(
        name="root",
        code_revision="1",
        description=description,
        inputs={
            "places": LocationDependency(provider="source"),
            "reference": LocationDependency(provider="reference"),
            "left": dependency,
            "right": Dependency(
                "value", producer="producer", parameters={"count": 6}
            ).bind(places=Input("reference")),
        },
        outputs={"value": table},
    )
    def root(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        left: pd.DataFrame,
        right: pd.DataFrame,
        *,
        parameters: Options,
    ) -> pd.DataFrame:
        del places, reference, left, right, parameters
        pytest.fail("Planning invoked the root")

    preferred = alternate if changes.get("preference") == "alternate" else first
    return {
        "metrics": PluginDefinition(metrics=[root, producer]),
        "first": PluginDefinition(location_operations=[first, reference_first]),
        "finish": PluginDefinition(location_operations=[finish]),
        "other_operations": PluginDefinition(
            location_operations=[alternate, initial, explicit, unused],
            route_preferences=[(preferred,), (preferred, finish)],
        ),
        **{
            name: PluginDefinition(
                resources={
                    name: provider(
                        id=provider_id,
                        code_revision=changes.get(name, "1"),
                        origins=(
                            origins
                            if origins is not None
                            else (
                                Origin(
                                    id="dataset", revision=changes.get("origin", "1")
                                ),
                            )
                        )
                        if name == "middle_provider"
                        else (),
                    )
                }
            )
            for name, provider_id in (
                ("source_provider", "source"),
                ("middle_provider", middle),
                ("target_provider", "target"),
                ("reference_provider", "reference"),
            )
        },
    }


def request(ids: tuple[str, ...] = ("a", "b")) -> dict:
    return {
        "metric": "root",
        "parameters": {"count": 99, "tags": ["caller"]},
        "inputs": {
            "places": {"provider": "source", "ids": list(ids)},
            "reference": {"provider": "reference", "ids": ["independent"]},
        },
    }
