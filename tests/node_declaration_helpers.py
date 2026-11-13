from typing import Any

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.planning.node_declarations import NodeDeclaration
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration

CENSUS = Origin(id="census", revision="2025")
ELEVATION = Origin(id="elevation")


class Tracts(FakeProvider):
    id = "tracts"
    schema = declaration()
    origins = (CENSUS,)


def provider(**attributes: object) -> type[Tracts]:
    return type("Provider", (Tracts,), attributes)


@metric(
    name="registry_placeholder",
    code_revision="1",
    description="Provider catalog.",
    outputs={
        "value": pa.DataFrameSchema(
            strict=True,
        )
    },
)
def placeholder() -> pd.DataFrame:
    return pd.DataFrame()


def plugin(**resources: type[BaseIO[Any]]) -> PluginDefinition:
    return PluginDefinition(metrics=[placeholder], resources=resources)


class Parameters(MetricParameters):
    year: int


def source(tracts: gpd.GeoDataFrame) -> None:
    pass


def operation(region: gpd.GeoDataFrame, *, parameters: Parameters) -> None:
    pass


def mixed(
    tracts: gpd.GeoDataFrame, table: pd.DataFrame, *, parameters: Parameters
) -> None:
    pass


def declarations() -> list[NodeDeclaration]:
    return [
        NodeDeclaration(
            "population",
            source,
            inputs={"tracts": LocationDependency(provider="tracts")},
            outputs=("population",),
            origins=(CENSUS,),
        ),
        NodeDeclaration(
            "select",
            operation,
            kind="location_operation",
            target="tracts",
            inputs={"region": LocationDependency(provider="tracts")},
        ),
        NodeDeclaration(
            "consumer",
            mixed,
            inputs={
                "tracts": LocationDependency(provider="tracts", primary=True),
                "table": Dependency("population", producer="population").bind(
                    tracts=LocationDependency(
                        operation="select",
                        parameters={"year": Input("parameters.year")},
                    ).bind(region=Input("tracts"))
                ),
            },
            outputs=("result",),
            origins=(
                CENSUS,
                ELEVATION,
                Origin(id="census", revision="2025", description="Duplicate"),
            ),
        ),
    ]
