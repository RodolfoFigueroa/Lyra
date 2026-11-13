"""Fresh receiving-route histories with deterministic in-process provider records."""

from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pydantic import Field

from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    MetricParameters,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.canonical_execution import CanonicalWorkload
from test_support.location_resolution import Provider, selection
from test_support.location_schema import column


class RouteParameters(MetricParameters):
    stride: int = Field(1, ge=1, description="Select IDs divisible by this stride")


def receiving_workload(rows: int, *, nested: bool) -> CanonicalWorkload:
    """Accept a source-bound summary whose measurement dependency requires targets."""

    class Target(Provider):
        id = "receiving_target"

    @location_operation(
        name="convert",
        code_revision="1",
        description="Convert source IDs to fixed target records",
        canonical=True,
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Target.id,
    )
    def convert(records: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key,) for key in records.location_id}

    @location_operation(
        name="filter_sources",
        code_revision="1",
        description="Retain source records by numeric ID",
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def filter_sources(
        records: gpd.GeoDataFrame, *, parameters: RouteParameters
    ) -> dict[str, tuple[str, ...]]:
        return {
            key: ((key,) if int(key) % parameters.stride == 0 else ())
            for key in records.location_id
        }

    @metric(
        name="measure",
        code_revision="1",
        description="Measure target records",
        inputs={"places": LocationDependency(provider=Target.id, primary=True)},
        outputs={
            "rows": pa.DataFrameSchema(
                {
                    "location_id": column(str),
                    "row_key": column(str),
                    "value": column(int),
                },
                strict=True,
                unique=["location_id", "row_key"],
            )
        },
    )
    def measure(places: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "location_id": places.location_id,
                "row_key": pd.Series("value", index=places.index, dtype="str"),
                "value": places.location_id.astype("int64"),
            }
        )

    source = Input("places")
    binding = (
        LocationDependency(
            operation="filter_sources",
            parameters={"stride": Input("parameters.stride")},
        ).bind(source=source)
        if nested
        else source
    )

    @metric(
        name="receiving",
        code_revision="1",
        description="Summarize target values from a source selection",
        inputs={
            "places": LocationDependency(provider=Provider.id),
            "values": Dependency("rows", producer="measure").bind(places=binding),
        },
        outputs={"total": pa.DataFrameSchema({"value": column(int)}, strict=True)},
    )
    def receiving(
        places: gpd.GeoDataFrame, values: pd.DataFrame, *, parameters: RouteParameters
    ) -> pd.DataFrame:
        del places, parameters
        return pd.DataFrame({"value": [int(values.value.sum())]})

    plugin = PluginDefinition(
        metrics=[measure, receiving],
        location_operations=[convert, filter_sources],
        resources={"source": Provider, "target": Target},
    )
    runner = LocalRunner.from_definitions({"bench": plugin})
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "receiving",
            "inputs": {"places": selection(*(str(i) for i in range(rows)))},
            "parameters": {"stride": 2},
        },
    )
    return CanonicalWorkload(
        GraphExecutor(runner.catalog, runner.catalog.implementations, Mock()), plan
    )
