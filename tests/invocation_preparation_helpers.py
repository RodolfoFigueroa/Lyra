"""Mixed bindings, ownership, and preparation through graph execution."""

from collections.abc import Mapping
from typing import Any

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from typing_extensions import override

from lyra.contracts.dependencies import Input
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.sdk import PluginDefinition, location_operation, metric
from lyra.sdk.dependencies import Dependency, LocationDependency
from test_support.location_operations import Places
from test_support.runtime_inputs import native_inputs
from tests.graph_arguments_helpers import Producer

_DEFAULT = object()


class PreparedPlaces(Places):
    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        frame = super().resolve(ids)
        frame["count"] = 3
        return frame


def invocation_inputs(values: Mapping[str, Any]) -> dict[str, Any]:
    """Build provider-scoped input records from the shared test data."""
    result = native_inputs(values)
    reference = capture_provider("places", PreparedPlaces).contract.reference
    for name, value in result.items():
        if isinstance(value, NativeLocationInput):
            frame = value.frame.rename(columns={value.id_column: "location_id"})
            if "count" not in frame:
                frame["count"] = 3
            result[name] = NativeLocationInput(frame, reference)
    return result


def implementation(
    seen: list[tuple[str, gpd.GeoDataFrame, Producer]] | None = None,
    operation_result: object = _DEFAULT,
) -> tuple[CompiledCatalog, dict[str, BoundPlugin]]:
    seen = [] if seen is None else seen

    @location_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=PreparedPlaces.id)},
        target_provider=PreparedPlaces.id,
        name="inner",
        description="Inner",
    )
    def inner(source: gpd.GeoDataFrame, *, parameters: Producer) -> object:
        seen.append(("inner", source, parameters))
        return (
            operation_result
            if operation_result is not _DEFAULT
            else dict.fromkeys(source.location_id, ("derived",))
        )

    @location_operation(
        code_revision="1",
        inputs={"source": LocationDependency(provider=PreparedPlaces.id)},
        target_provider=PreparedPlaces.id,
        name="outer",
        description="Outer",
    )
    def outer(
        source: gpd.GeoDataFrame, *, parameters: Producer
    ) -> dict[str, tuple[str, ...]]:
        seen.append(("outer", source, parameters))
        return {key: (key,) for key in source.location_id}

    @metric(
        code_revision="1",
        inputs={"source": LocationDependency(provider=PreparedPlaces.id)},
        name="leaf",
        description="Leaf",
        outputs={"leaf": pa.DataFrameSchema({}, strict=True)},
    )
    def leaf(source: gpd.GeoDataFrame, parameters: Producer) -> pd.DataFrame:
        seen.append(("leaf", source, parameters))
        return pd.DataFrame()

    @metric(
        code_revision="1",
        name="middle",
        description="Middle",
        outputs={"middle": pa.DataFrameSchema({}, strict=True)},
        inputs={
            "source": LocationDependency(provider=PreparedPlaces.id),
            "table": Dependency("leaf", producer="leaf"),
        },
    )
    def middle(
        source: gpd.GeoDataFrame, parameters: Producer, table: pd.DataFrame
    ) -> pd.DataFrame:
        seen.append(("middle", source, parameters))
        return table

    binding = LocationDependency(
        "outer",
        parameters={"year": 1980},
    ).bind(
        source=LocationDependency(
            "inner",
            parameters={"year": Input("parameters.year")},
        ).bind(source=Input("alias"))
    )

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": pa.DataFrameSchema({}, strict=True)},
        inputs={
            "source": LocationDependency(provider=PreparedPlaces.id),
            "alias": LocationDependency(provider=PreparedPlaces.id),
            "first": Dependency(
                "middle",
                parameters={"year": 1990},
                producer="middle",
            ).bind(source=binding),
            "second": Dependency(
                "middle",
                parameters={"year": 1990, "count": 2},
                producer="middle",
            ).bind(source=binding),
        },
    )
    def root(
        source: gpd.GeoDataFrame,
        alias: gpd.GeoDataFrame,
        parameters: Producer,
        first: pd.DataFrame,
        second: pd.DataFrame,
    ) -> pd.DataFrame:
        assert isinstance(alias, gpd.GeoDataFrame)
        seen.append(("root", source, parameters))
        pd.testing.assert_frame_equal(first, second)
        return first

    plugins = {
        "root": PluginDefinition(metrics=[root]),
        "tables": PluginDefinition(metrics=[middle, leaf]),
        "operations": PluginDefinition(
            location_operations=[inner, outer], resources={"places": PreparedPlaces}
        ),
    }
    catalog = LocalRunner.from_definitions(plugins).catalog
    return catalog, dict(catalog.implementations)
