"""Live graph compilation remains service-free and preserves binding sites."""

from collections.abc import Callable

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.table_fixtures import value_schema
from tests.node_declaration_helpers import CENSUS, Tracts, provider


class Parameters(MetricParameters):
    year: int = Field(default=2025, description="Selection year.")


def plugins(
    binding: Dependency | None = None,
    *,
    target: str = "counties",
    seed: LocationDependency | None = None,
) -> dict[str, PluginDefinition]:
    @location_operation(
        name="select",
        code_revision="1",
        description="Select targets.",
        inputs={"source": LocationDependency(provider="tracts")},
        target_provider=target,
    )
    def select(records: gpd.GeoDataFrame, *, parameters: Parameters) -> pd.DataFrame:
        del records, parameters
        pytest.fail("handler called during compilation")

    @metric(
        name="producer",
        code_revision="1",
        description="Private producer.",
        private=True,
        inputs={"places": LocationDependency(provider="counties")},
        outputs={"value": value_schema()},
        origins=[CENSUS],
    )
    def producer(places: gpd.GeoDataFrame, *, parameters: Parameters) -> pd.DataFrame:
        del places, parameters
        pytest.fail("handler called during compilation")

    def use(year: int) -> Dependency:
        return Dependency("value", producer="producer", parameters={"year": year}).bind(
            places=LocationDependency(
                "select", parameters={"year": Input("parameters.year")}
            ).bind(source=Input("seed"))
        )

    @metric(
        name="consumer",
        code_revision="1",
        description="Public consumer.",
        inputs={
            "seed": seed or LocationDependency(provider="tracts"),
            "first": binding or use(2020),
            "second": use(2021),
        },
        outputs={"result": value_schema()},
    )
    def consumer(
        seed: gpd.GeoDataFrame,
        first: pd.DataFrame,
        second: pd.DataFrame,
        *,
        parameters: Parameters,
    ) -> pd.DataFrame:
        del seed, first, second, parameters
        pytest.fail("handler called during compilation")

    return {
        "providers": PluginDefinition(
            resources={"tracts": Tracts, "counties": provider(id="counties")}
        ),
        "upstream": PluginDefinition(metrics=[producer], location_operations=[select]),
        "downstream": PluginDefinition(metrics=[consumer]),
    }


def operation(
    name: str, source: str, target: str, *, canonical: bool = True
) -> Callable:
    @location_operation(
        name=name,
        code_revision="1",
        description="Select targets.",
        inputs={"source": LocationDependency(provider=source)},
        target_provider=target,
        canonical=canonical,
    )
    def select(source: gpd.GeoDataFrame) -> pd.DataFrame:
        del source
        pytest.fail("Preparation invoked an operation")

    return select
