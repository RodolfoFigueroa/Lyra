"""Deterministic shared references and dense immediate correspondences."""

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from typing_extensions import override

from lyra.contracts.metric_scope import CachePolicy
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.cache_workloads import CacheWorkload
from test_support.location_resolution import Provider
from test_support.location_schema import column


def retained_workload(
    *, derived: bool, policy: CachePolicy = "per_location", revision: str = "1"
) -> CacheWorkload:
    calls: list[tuple[str, frozenset[str]]] = []

    class Places(Provider):
        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            calls.append(("acquire", frozenset(ids)))
            return super().resolve(ids)

    Places.code_revision = revision

    @location_operation(
        name="related",
        description="One hundred shared targets for each immediate source",
        cache=policy,
        inputs={"source": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def related(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append(("related", frozenset(source.location_id)))
        return dict.fromkeys(source.location_id, tuple(f"t{i}" for i in range(100)))

    inputs = {
        "main": LocationDependency(provider=Provider.id, primary=True),
        "reference": LocationDependency(provider=Provider.id),
    }
    if derived:
        inputs["targets"] = LocationDependency(operation="related").bind(
            source=Input("main")
        )

    def values(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        calls.append(("root", frozenset(main.location_id)))
        return pd.DataFrame(
            {"location_id": main.location_id, "row_key": "one", "value": len(reference)}
        )

    def combined(
        main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame, targets: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        assert len(targets) == len(main) * 100
        return values(main, reference)

    root = metric(
        name="root",
        description="Partitions with a complete independent reference selection",
        cache="per_location",
        inputs=inputs,
        outputs={
            "data": pa.DataFrameSchema(
                {
                    "location_id": column(str),
                    "row_key": column(str),
                    "value": column(int),
                },
                strict=True,
                unique=["location_id", "row_key"],
            )
        },
    )(combined if derived else values)
    return CacheWorkload(
        PluginDefinition(
            metrics=[root], location_operations=[related], resources={"places": Places}
        ),
        calls,
    )


def retained_inputs(partitions: int, references: int = 10000) -> dict[str, object]:
    return {
        "main": {"provider": Provider.id, "ids": [f"s{i}" for i in range(partitions)]},
        "reference": {
            "provider": Provider.id,
            "ids": [f"r{i}" for i in range(references)],
        },
    }
