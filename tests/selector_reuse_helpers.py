"""Selector cache workload using an in-process managed query boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import ibis.expr.types as ir
import pandas as pd
from pydantic import Field
from typing_extensions import override

from lyra.contracts.interventions import AcceptedIntervention
from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.origins import Origin
from lyra.data.interventions import Intervention
from lyra.runtime.execution import ExecutionOutcome, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.managed_provider import ManagedLocationProvider
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.provider_schema import ProviderSchema
from test_support.location_operations import CatalogPlaces, Parameters, Places
from tests.cache_helpers import SCHEMA


@dataclass(frozen=True)
class Identity:
    revision: str = "1"
    target_revision: str = "1"
    origin_revision: str = "1"
    upstream_revision: str | None = None
    operation_count: int | None = None


class ConsumerParameters(MetricParameters):
    metric_count: int = Field(default=2, description="Metric-only output value")


DEFAULT_IDENTITY = Identity()


def selector_definitions(
    *,
    policy: CachePolicy = "per_location",
    target_cache: bool = True,
    metric_cache: CachePolicy = "off",
    identity: Identity = DEFAULT_IDENTITY,
) -> tuple[dict[str, PluginDefinition], list, list]:
    events = []
    seen = []
    selected = []

    class Source(CatalogPlaces):
        id = "sources"

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(("source", frozenset(ids)))
            return super().resolve(ids)

    class Target(CatalogPlaces, ManagedLocationProvider):
        id = "targets"
        cache = target_cache
        code_revision = identity.target_revision
        origins = (Origin(id="catalog", revision=identity.origin_revision),)

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append(("exact", frozenset(ids)))
            return super().resolve(ids)

        @override
        def select(
            self, expression: ir.Table, schema: ProviderSchema
        ) -> gpd.GeoDataFrame:
            assert expression.columns == tuple(schema.schema.columns)
            events.append(("query", frozenset(selected)))
            # Fake managed boundary returns the complete selected pool, including
            # an unused record. It never consults the provider-record cache.
            return (
                Places.resolve(self, [*selected, "unused"])
                if selected
                else schema.empty_frame()
            )

        @override
        def select_effective(
            self,
            expression: ir.Table,
            schema: ProviderSchema,
            intervention: AcceptedIntervention,
        ) -> gpd.GeoDataFrame:
            pool = self.select(expression, schema)
            assert not intervention.add
            return pool.loc[~pool.location_id.isin(intervention.remove)]

    def selector(
        records: gpd.GeoDataFrame, relation: ir.Table, *, parameters: Parameters
    ) -> ir.Table:
        events.append(("selector", frozenset(records.location_id)))
        selected[:] = [
            f"{key}-{parameters.count}" for key in records.location_id if key != "empty"
        ]
        return relation.filter(
            relation.location_id.isin([*selected, "unused"] if selected else [])
        )

    @location_operation(
        name="mapping",
        description="Select independent targets",
        cache=policy,
        code_revision=identity.revision,
        inputs={"source": LocationDependency(provider=Source.id)},
        target_provider=Target.id,
        target_selector=selector,
    )
    def mapping(
        rows: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict:
        events.append(("mapping", frozenset(rows.location_id)))
        groups = {
            key: (f"{key}-{parameters.count}",) if key != "empty" else ()
            for key in rows.location_id
        }
        assert {target for ids in groups.values() for target in ids}.issubset(
            candidates.location_id
        )
        return groups

    @location_operation(
        name="upstream",
        description="Preserve source IDs",
        cache="per_location",
        code_revision=identity.upstream_revision or "1",
        inputs={"source": LocationDependency(provider=Source.id)},
        target_provider=Source.id,
    )
    def upstream(source: gpd.GeoDataFrame) -> dict:
        events.append(("upstream", frozenset(source.location_id)))
        return {key: (key,) for key in source.location_id}

    source_binding = (
        LocationDependency(operation="upstream").bind(source=Input("source"))
        if identity.upstream_revision is not None
        else Input("source")
    )

    @metric(
        name="consumer",
        description="Consume selected records",
        cache=metric_cache,
        inputs={
            "source": LocationDependency(provider=Source.id),
            "places": LocationDependency(
                operation="mapping",
                parameters={}
                if identity.operation_count is None
                else {"count": identity.operation_count},
            ).bind(source=source_binding),
        },
        outputs={"result": SCHEMA},
    )
    def consumer(
        source: gpd.GeoDataFrame,
        places: gpd.GeoDataFrame,
        parameters: ConsumerParameters,
    ) -> pd.DataFrame:
        assert set(places.source_location_id).issubset(source.location_id)
        seen.append(
            set(zip(places.source_location_id, places.location_id, strict=True))
        )
        events.append(("metric", parameters.metric_count))
        return pd.DataFrame({"value": [parameters.metric_count]})

    return (
        {
            "test": PluginDefinition(
                metrics=[consumer, exact_metric(seen)],
                location_operations=[mapping, upstream],
                resources={"source": Source, "target": Target},
            )
        },
        events,
        seen,
    )


def workload(
    tmp_path: Path,
    *,
    policy: CachePolicy = "per_location",
    target_cache: bool = True,
    metric_cache: CachePolicy = "off",
    identity: Identity = DEFAULT_IDENTITY,
) -> tuple[Callable[..., ExecutionOutcome], list, list]:
    definitions, events, seen = selector_definitions(
        policy=policy,
        target_cache=target_cache,
        metric_cache=metric_cache,
        identity=identity,
    )
    runner = LocalRunner.from_definitions(definitions)
    executor = GraphExecutor(runner.catalog, Mock())
    executor.store = SQLiteStore(tmp_path / "values.sqlite")
    executor.provider_records = ProviderRecords(executor.store)

    def run(
        ids: tuple[str, ...] = ("a", "b"),
        parameters: dict | None = None,
        *,
        exact: bool = False,
        interventions: list[Intervention] | None = None,
    ) -> ExecutionOutcome:
        request = {
            "metric": "consumer",
            "inputs": {"source": {"provider": "sources", "ids": list(ids)}},
            "parameters": parameters or {},
        }
        if exact:
            request = {
                "metric": "exact_consumer",
                "inputs": {"places": {"provider": "targets", "ids": list(ids)}},
            }
        return executor.execute(
            prepare_execution(
                runner.catalog, request, native_interventions=interventions
            )
        )

    return run, events, seen


def exact_metric(seen: list) -> Callable:
    @metric(
        name="exact_consumer",
        description="Read targets by ordinary record identity",
        inputs={"places": LocationDependency(provider="targets")},
        outputs={"result": SCHEMA},
    )
    def exact_consumer(places: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(set(places.location_id))
        return pd.DataFrame({"value": [len(places)]})

    return exact_consumer
