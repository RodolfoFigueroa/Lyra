"""Partial consumer hits preserve producer scope and complete shared context."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk import Dependency, Input, LocationDependency, PluginDefinition, metric
from tests.cache_helpers import SCHEMA
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("main_ids", [("a", "b"), ("a", "b", "c")])
@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("references", [("x", "y"), ("a", "b")])
def test_dependency_demand(
    tmp_path: Path,
    policy: CachePolicy,
    references: tuple[str, str],
    main_ids: tuple[str, ...],
    *,
    explicit: bool,
) -> None:
    calls = []
    producer_calls = []
    consumers = []
    events = []

    @metric(
        name="areas",
        description="Areas",
        cache=policy,
        inputs={"main": LocationDependency(provider="target", primary=True)},
        outputs={"data": schema()},
    )
    def areas(main: gpd.GeoDataFrame) -> pd.DataFrame:
        ids = set(main.location_id)
        calls.append(ids)
        producer_calls.append(ids)
        # Full/off output deliberately depends on the complete bound batch.
        return rows(ids, 1 if policy == "per_location" else len(ids))

    @metric(
        name="summary",
        description="Whole batch context",
        cache="full",
        inputs={"main": LocationDependency(provider="target")},
        outputs={"data": SCHEMA},
    )
    def summary(main: gpd.GeoDataFrame) -> pd.DataFrame:
        calls.append(set(main.location_id))
        return pd.DataFrame({"value": [len(main)]})

    @metric(
        name="independent",
        description="Non-primary binding",
        cache=policy,
        inputs={
            "main": LocationDependency(provider="target"),
            "reference": LocationDependency(provider="target", primary=True),
        },
        outputs={"data": schema()},
    )
    def independent(
        main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        calls.append(set(reference.location_id))
        assert set(main.location_id) == set(main_ids)
        assert set(reference.location_id) == (
            {"z"} if policy == "per_location" and "z" in references else set(references)
        )
        return rows(set(reference.location_id), len(main))

    related = Dependency("data", producer="areas")
    if explicit:
        related = related.bind(main=Input("main"))

    @metric(
        name="comparison",
        description="Compare areas",
        cache="per_location",
        inputs={
            "main": LocationDependency(provider="target", primary=True),
            "reference": LocationDependency(provider="target"),
            "area": related,
            "whole": Dependency("data", producer="summary"),
            "independent": Dependency("data", producer="independent"),
        },
        outputs={"data": schema()},
    )
    def comparison(
        main: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        area: pd.DataFrame,
        whole: pd.DataFrame,
        independent: pd.DataFrame,
    ) -> pd.DataFrame:
        consumers.append(set(main.location_id))
        assert set(area.location_id) == set(main.location_id)
        assert set(reference.location_id) == set(references)
        assert set(independent.location_id) == set(references)
        assert set(independent.value) == {len(main_ids)}
        assert whole.value.tolist() == [len(main_ids)]
        assert set(area.value) == {1 if policy == "per_location" else len(main_ids)}
        return area

    definitions = provider_execution_definitions(events)
    definitions["metrics"] = PluginDefinition(
        metrics=[areas, summary, independent, comparison]
    )
    executor = make_executor(tmp_path, definitions)

    def prepared(refs: tuple[str, str]) -> PreparedExecution:
        return prepare_execution(
            executor.catalog,
            {
                "metric": "comparison",
                "inputs": {
                    "main": {"provider": "target", "ids": list(main_ids)},
                    "reference": {"provider": "target", "ids": list(refs)},
                },
            },
        )

    warmed = policy == "per_location" and explicit
    if warmed:
        executor.execute(
            prepare_execution(
                executor.catalog,
                {
                    "metric": "areas",
                    "inputs": {"main": {"provider": "target", "ids": ["b"]}},
                },
            )
        )
        producer_calls.clear()
    request = prepared(references)
    keys = seed_consumer(
        executor, request, 1 if policy == "per_location" else len(main_ids)
    )
    assert set(executor.execute(request).outputs["data"].location_id) == set(main_ids)
    assert consumers == [set(main_ids) - {"a"}]
    expected = (
        set(main_ids) - ({"a", "b"} if warmed else {"a"})
        if policy == "per_location"
        else set(main_ids)
    )
    assert producer_calls == ([expected] if expected else [])
    assert set(main_ids) in calls  # The other diamond branch needs every record.
    assert set(references) in calls
    calls.clear()
    consumers.clear()
    events.clear()
    executor.execute(request)
    assert calls == consumers == events == []
    references = (references[0], "z")
    assert (
        executor.validate_plan(prepared(references)).demand().computation_keys != keys
    )
    executor.execute(prepared(references))
    assert consumers == [set(main_ids)]


def rows(ids: set[str], value: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "location_id": sorted(ids),
            "row_key": ["area"] * len(ids),
            "value": pd.array([value] * len(ids), dtype="Int64"),
        }
    )


def make_executor(
    tmp_path: Path, definitions: dict[str, PluginDefinition]
) -> GraphExecutor:
    local = LocalRunner.from_definitions(definitions)
    return GraphExecutor(
        local.catalog,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )


def seed_consumer(
    executor: GraphExecutor, request: PreparedExecution, value: int
) -> dict[str | None, str]:
    bound = executor.validate_plan(request)
    keys = bound.demand().computation_keys
    assert keys is not None
    assert executor.store is not None
    # Seed A under the original binding, leaving the other groups missing.
    MetricTables(executor.store).publish(
        (
            (
                Header(
                    value_key=keys["a"],
                    node_id="comparison",
                    output_scope="location",
                    owner_scope=bound.locations["main"].provider.provider_scope,
                ),
                ("a",),
            ),
        ),
        {"data": rows({"a"}, value)},
        executor.catalog.implementations["metrics"].output_storage("comparison"),
        location_inputs={
            Header(
                value_key=keys["a"],
                node_id="comparison",
                output_scope="location",
                owner_scope=bound.locations["main"].provider.provider_scope,
            ).value_key: InputBindings()
        },
    )
    return keys
