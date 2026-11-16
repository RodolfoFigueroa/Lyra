"""Isolated retained-result publication fixtures."""

import geopandas as gpd
import pandas as pd

from lyra.contracts.origins import Origin
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.results import LocationInputDescription
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


def location_descriptions() -> dict[str, LocationInputDescription]:
    """Build retained metadata without acquiring or retaining any input values."""
    scope = ProviderScope(
        provider_id="places",
        code_revision="survey",
        origins=frozenset({Origin(id="survey", revision="1", description="Survey")}),
    )
    table_schema = OutputMetadata(
        geometry="geometry",
        columns=[
            ColumnMetadata(
                name="location_id", dtype="str", unit=None, description="Location"
            ),
            ColumnMetadata(
                name="height",
                dtype="float64",
                unit="m",
                nullable=True,
                description="Height",
            ),
            ColumnMetadata(
                name="geometry",
                dtype="geometry",
                parameters={"crs": "EPSG:32614"},
                unit=None,
                description="Geometry",
            ),
        ],
    )
    direct = LocationInputDescription(
        kind="provider",
        table_schema=table_schema,
        provider_scope=scope,
        availability="available",
    )
    forwarding = table_schema.model_copy(deep=True)
    forwarding.columns.insert(
        0,
        ColumnMetadata(
            name="source_location_id", dtype="str", unit=None, description="Source"
        ),
    )
    return {
        "blocks": direct,
        "known_empty": direct.model_copy(deep=True),
        "reference_blocks": direct.model_copy(
            deep=True, update={"availability": "not_materialized"}
        ),
        "nearby_trees": LocationInputDescription(
            kind="correspondence",
            table_schema=forwarding,
            provider_scope=ProviderScope(provider_id="trees", code_revision="2"),
            source_scope=scope,
            availability="available",
        ),
    }


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
