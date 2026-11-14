"""Isolated retained-result publication fixtures."""

import geopandas as gpd
import pandas as pd

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultOutput,
)
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.output_schema import compile_output
from lyra.sdk.storage_schema import describe, empty_frame
from tests.metric_execution_helpers import schema


def publish_values(
    store: SQLiteStore, keys: tuple[str, ...], *, spatial: bool = False
) -> ResultDescription:
    declaration = schema(spatial=spatial)
    layout = describe(declaration, metadata=compile_output(declaration)[1])
    owner = ProviderScope(provider_id="places", code_revision="survey")
    registrations = tuple(
        Registration.from_description("metric", name, layout) for name in ("a", "b")
    )
    store.initialize(registrations)
    for key in keys:
        frame = pd.DataFrame(
            {
                "location_id": [key],
                "row_key": ["r"],
                "value": pd.array([1], dtype="Int64"),
            }
        )
        if spatial:
            frame = gpd.GeoDataFrame(
                frame, geometry=gpd.points_from_xy([0], [0]), crs=4326
            )
        MetricTables(store).publish(
            Header(
                value_key=key,
                node_id="metric",
                output_scope="location",
                owner_scope=owner,
            ),
            {"a": frame, "b": empty_frame(declaration)},
            {"a": layout, "b": layout},
            (key, "empty"),
        )
    return ResultDescription(
        metric_name="metric",
        output_names=("a", "b"),
        code_revision="v1",
        parameters={"default": None, "nested": {"list": [1, 2]}},
        outputs={
            r.output_name: ResultOutput(
                registration_key=r.key,
                owner_scope=owner,
                computations=tuple(
                    ComputationSelection(value_key=key, groups=(key,)) for key in keys
                ),
            )
            for r in registrations
        },
    )
