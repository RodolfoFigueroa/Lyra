"""Operation relationships and shared summaries under reduced runtime demand."""

from dataclasses import replace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.resources import IOConfig
from test_support.location_operations import Places
from test_support.location_schema import column
from tests.metric_execution_helpers import schema


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("demand", [("B",), ("A", "B"), ()])
@pytest.mark.parametrize("chain", [False, True])
def test_related_operation_groups(
    policy: CachePolicy, demand: tuple[str, ...], *, chain: bool
) -> None:
    seen = []

    @location_operation(
        name="neighbors",
        description="Neighbors",
        code_revision="1",
        cache=policy,
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def neighbors(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {
            key: {"A": ("X", "Y"), "B": ("Y", "Z")}[key] for key in source.location_id
        }

    @location_operation(
        name="next",
        description="Next hop",
        code_revision="1",
        cache=policy,
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def next_hop(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {key: (key.lower(),) for key in source.location_id}

    binding = LocationDependency(operation="neighbors").bind(source=Input("main"))
    if chain:
        binding = LocationDependency(operation="next").bind(source=binding)

    @metric(
        name="measure",
        description="Related measurements",
        code_revision="1",
        cache=policy,
        inputs={
            "main": LocationDependency(provider=Places.id, primary=True),
            "references": binding,
        },
        outputs={"data": schema()},
    )
    def measure(main: gpd.GeoDataFrame, references: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(
            (
                set(main.location_id),
                set(
                    zip(
                        references.source_location_id,
                        references.location_id,
                        strict=True,
                    )
                ),
            )
        )
        return pd.DataFrame(
            {"location_id": main.location_id, "row_key": "one", "value": 1}
        )

    @metric(
        name="root",
        description="Consumer",
        code_revision="1",
        inputs={
            "main": LocationDependency(provider=Places.id, primary=True),
            "data": Dependency("data", producer="measure"),
        },
        outputs={"data": schema()},
    )
    def root(main: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert set(data.location_id) == set(main.location_id) == set(demand)
        return data

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[root, measure],
                location_operations=[neighbors, next_hop],
                resources={"places": Places},
            )
        }
    )
    plugin = runner.catalog.implementations["test"]
    value = NativeLocationInput(
        Places(IOConfig(), {}).resolve(["A", "B"]),
        runner.catalog.provider_registry[Places.id].contract.reference,
    )
    invocation = plugin.prepare_resolved_invocation(
        "root", {"main": value}
    ).select_primary("main", frozenset(demand))
    outcome = GraphExecutor(runner.catalog, Mock()).execute(invocation)
    assert set(outcome.outputs["data"].location_id) == set(demand)
    owners = set(demand) if policy == "per_location" else {"A", "B"}
    if owners:
        pairs = {
            (source, target)
            for source in owners
            for target in {"A": ("X", "Y"), "B": ("Y", "Z")}[source]
        }
        if chain:
            pairs = {(target, target.lower()) for _, target in pairs}
        assert seen == [(owners, pairs)]
    else:
        assert not seen


@pytest.mark.parametrize("ids", [("A", "B", "C"), ("C", "B", "A"), ()])
def test_hybrid_summary_retains_complete_context(ids: tuple[str, ...]) -> None:
    seen = []
    summary_schema = pa.DataFrameSchema(
        {"label": column(str), "count": column(int)}, strict=True
    )

    @metric(
        name="areas",
        description="Areas",
        code_revision="1",
        cache="per_location",
        inputs={"main": LocationDependency(provider=Places.id, primary=True)},
        outputs={"data": schema()},
    )
    def areas(main: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {"location_id": main.location_id, "row_key": "one", "value": 1}
        )

    @metric(
        name="summary",
        description="Whole summary",
        code_revision="1",
        inputs={
            "main": LocationDependency(provider=Places.id),
            "areas": Dependency("data", producer="areas"),
        },
        outputs={"data": summary_schema},
    )
    def summary(main: gpd.GeoDataFrame, areas: pd.DataFrame) -> pd.DataFrame:
        seen.append(set(main.location_id))
        assert len(areas) == len(ids)
        return pd.DataFrame({"label": ["A"], "count": [len(areas)]})

    @metric(
        name="root",
        description="Flags",
        code_revision="1",
        inputs={
            "main": LocationDependency(provider=Places.id, primary=True),
            "areas": Dependency("data", producer="areas"),
            "summary": Dependency("data", producer="summary"),
        },
        outputs={"data": schema()},
    )
    def root(
        main: gpd.GeoDataFrame, areas: pd.DataFrame, summary: pd.DataFrame
    ) -> pd.DataFrame:
        assert set(main.location_id) == set(areas.location_id) == {"B"}
        assert summary.to_dict("records") == [{"label": "A", "count": 3}]
        return areas

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[root, areas, summary], resources={"places": Places}
            )
        }
    )
    value = NativeLocationInput(
        Places(IOConfig(), {}).resolve(list(ids)),
        runner.catalog.provider_registry[Places.id].contract.reference,
    )
    invocation = runner.catalog.implementations["test"].prepare_resolved_invocation(
        "root", {"main": value}
    )
    demand = frozenset({"B"}) if ids else frozenset()
    reduced = invocation.select_primary("main", demand)
    outcome = GraphExecutor(runner.catalog, Mock()).execute(reduced)
    assert set(outcome.outputs["data"].location_id) == set(demand)
    assert seen == [set(ids)]
    permuted = replace(
        invocation,
        parameters=None,
        locations={"main": replace(value, frame=value.frame.iloc[::-1])},
    )
    assert permuted.select_primary("main", demand).identity == reduced.identity
    if ids:
        changed = replace(
            invocation,
            parameters=None,
            locations={"main": replace(value, frame=value.frame.iloc[:2])},
        )
        assert changed.select_primary("main", demand).identity != reduced.identity


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_operation_primary_keeps_every_incoming_owner_pair(policy: CachePolicy) -> None:
    seen = []

    @location_operation(
        name="neighbors",
        description="Neighbors",
        code_revision="1",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def neighbors(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        assert set(source.location_id) == {"A", "B"}
        return {"A": ("X", "Y"), "B": ("Y", "Z")}

    @metric(
        name="measure",
        description="Target owners",
        code_revision="1",
        cache=policy,
        inputs={"targets": LocationDependency(provider=Places.id, primary=True)},
        outputs={"data": schema()},
    )
    def measure(targets: gpd.GeoDataFrame) -> pd.DataFrame:
        seen.append(
            set(zip(targets.source_location_id, targets.location_id, strict=True))
        )
        return pd.DataFrame(
            {
                "location_id": targets.location_id,
                "row_key": targets.source_location_id,
                "value": 1,
            }
        )

    @metric(
        name="root",
        description="Source comparison",
        code_revision="1",
        inputs={
            "main": LocationDependency(provider=Places.id, primary=True),
            "data": Dependency("data", producer="measure").bind(
                targets=LocationDependency(operation="neighbors").bind(
                    source=Input("main")
                )
            ),
        },
        outputs={"data": schema()},
    )
    def root(main: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        assert set(zip(data.row_key, data.location_id, strict=True)) == {
            ("A", "Y"),
            ("B", "Y"),
            ("B", "Z"),
        }
        return pd.DataFrame(
            {"location_id": main.location_id, "row_key": "one", "value": len(data)}
        )

    runner = LocalRunner.from_definitions(
        {
            "test": PluginDefinition(
                metrics=[root, measure],
                location_operations=[neighbors],
                resources={"places": Places},
            )
        }
    )
    value = NativeLocationInput(
        Places(IOConfig(), {}).resolve(["A", "B"]),
        runner.catalog.provider_registry[Places.id].contract.reference,
    )
    invocation = (
        runner.catalog.implementations["test"]
        .prepare_resolved_invocation("root", {"main": value})
        .select_primary("main", frozenset({"B"}))
    )
    result = GraphExecutor(runner.catalog, Mock()).execute(invocation)
    assert result.outputs["data"].value.tolist() == [3]
    expected = {("A", "Y"), ("B", "Y"), ("B", "Z")}
    if policy != "per_location":
        expected.add(("A", "X"))
    assert seen == [expected]
