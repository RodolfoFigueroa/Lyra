"""In-process source and target providers for effective input tests."""

from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
from typing_extensions import override

from lyra.sdk import (
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_schema import column
from tests.cache_helpers import SCHEMA
from tests.provider_schema_helpers import declaration, frame


def records() -> gpd.GeoDataFrame:
    return frame().assign(target_id=["a", "b"])


def workload(trace: Mock) -> dict[str, PluginDefinition]:
    class Source(LocationProvider):
        id = "sources"
        cache = True
        schema = declaration().add_columns({"target_id": column(str)})

        @override
        def existing_ids(self, ids: list[str]) -> set[str]:
            trace.membership(self.id, ids)
            return {"a", "b"}.intersection(ids)

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            trace.resolve(self.id, ids)
            return records().loc[lambda df: df.location_id.isin(ids)]

    class Target(Source):
        id = "targets"

    @location_operation(
        name="follow",
        description="Follow source target IDs",
        cache="full",
        inputs={"source": LocationDependency(provider="sources")},
        target_provider="targets",
    )
    def follow(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        trace.follow(source.location_id.tolist())
        groups: dict[str, tuple[str, ...]] = {
            key: (target,)
            for key, target in zip(source.location_id, source.target_id, strict=True)
        }
        source.loc[:, "target_id"] = "mutated"
        return groups

    @metric(
        name="root",
        description="Sum the linked records",
        cache="full",
        inputs={
            "source": LocationDependency(provider="sources"),
            "linked": LocationDependency("follow").bind(source=Input("source")),
        },
        outputs={"rows": SCHEMA},
    )
    def measure(source: gpd.GeoDataFrame, linked: gpd.GeoDataFrame) -> pd.DataFrame:
        pairs = list(zip(linked.source_location_id, linked.location_id, strict=True))
        assert pairs == list(zip(source.location_id, source.target_id, strict=True))
        trace.measure(pairs)
        total = linked["count"].sum()
        linked.loc[:, "count"] = -100
        return pd.DataFrame({"value": [int(total)]})

    return {
        "test": PluginDefinition(
            metrics=[measure],
            location_operations=[follow],
            resources={"sources": Source, "targets": Target},
        )
    }


def inputs(*ids: str) -> dict:
    return {"source": {"provider": "sources", "ids": list(ids)}}
