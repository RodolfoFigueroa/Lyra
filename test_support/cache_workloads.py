"""Independent partitions with related operations and complete reference context."""

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa

from lyra.sdk import (
    Dependency,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_resolution import Provider


@dataclass
class CacheWorkload:
    plugin: PluginDefinition
    calls: list[tuple[str, frozenset[str]]]


def cache_workload(*, hybrid: bool, revision: str = "1") -> CacheWorkload:
    calls: list[tuple[str, frozenset[str]]] = []
    schema = pa.DataFrameSchema(
        {
            name: pa.Column(dtype, description=name, metadata={"unit": None})
            for name, dtype in [
                ("location_id", "string"),
                ("row_key", "string"),
                ("value", "Int64"),
            ]
        },
        strict=True,
        unique=["location_id", "row_key"],
    )

    @location_operation(
        name="related",
        description="Related records",
        cache="per_location",
        inputs={"main": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def related(main: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append(("related", frozenset(main.location_id)))
        return {key: (key,) for key in main.location_id}

    @metric(
        name="context",
        description="Complete reference count",
        cache="full",
        inputs={"reference": LocationDependency(provider=Provider.id)},
        outputs={
            "total": pa.DataFrameSchema(
                {
                    "value": pa.Column(
                        "Int64", description="Count", metadata={"unit": None}
                    )
                },
                strict=True,
            )
        },
    )
    def context(reference: gpd.GeoDataFrame) -> pd.DataFrame:
        calls.append(("context", frozenset(reference.location_id)))
        return pd.DataFrame({"value": pd.array([len(reference)], dtype="Int64")})

    inputs: dict[str, LocationDependency | Dependency] = {
        "main": LocationDependency(provider=Provider.id, primary=True)
    }
    if hybrid:
        inputs |= {
            "targets": LocationDependency(operation="related"),
            "reference": LocationDependency(provider=Provider.id),
            "whole": Dependency("total", producer="context"),
        }

    def values(main: gpd.GeoDataFrame, value: int) -> pd.DataFrame:
        calls.append(("root", frozenset(main.location_id)))
        return pd.DataFrame(
            {
                "location_id": pd.array(list(main.location_id), dtype="string"),
                "row_key": pd.array(["value"] * len(main), dtype="string"),
                "value": pd.array([value] * len(main), dtype="Int64"),
            }
        )

    def direct(main: gpd.GeoDataFrame) -> pd.DataFrame:
        return values(main, 1)

    def combined(
        main: gpd.GeoDataFrame,
        targets: gpd.GeoDataFrame,
        whole: pd.DataFrame,
        reference: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert set(reference.location_id) == {"x", "y"}
        assert set(targets.location_id) == set(main.location_id)
        return values(main, int(whole.value.iloc[0]))

    root = metric(
        name="root",
        description="Partition values",
        code_revision=revision,
        cache="per_location",
        inputs=inputs,
        outputs={"data": schema},
    )(combined if hybrid else direct)

    return CacheWorkload(
        PluginDefinition(
            metrics=[root, context],
            location_operations=[related],
            resources={"places": Provider},
        ),
        calls,
    )


def cache_inputs(start: int, rows: int, *, hybrid: bool) -> dict[str, object]:
    inputs: dict[str, object] = {
        "main": {
            "provider": Provider.id,
            "ids": [str(i) for i in range(start, start + rows)],
        }
    }
    if hybrid:
        inputs["reference"] = {"provider": Provider.id, "ids": ["x", "y"]}
    return inputs
