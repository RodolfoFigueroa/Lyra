"""Reduced demand follows receiving routes while complete contexts stay independent."""

from dataclasses import replace
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import Dependency, Input, LocationDependency, PluginDefinition, metric
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RESULT
from tests.receiving_route_helpers import Trace, definitions


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("binding", ["matching", "explicit", "renamed"])
@pytest.mark.parametrize("demand", [("b",), ()])
def test_receiving_route_selection_and_independent_uses(
    policy: CachePolicy, binding: str, demand: tuple[str, ...]
) -> None:
    trace = Trace()
    plugins = definitions(trace, policy=policy)
    producer = "renamed" if binding == "renamed" else "producer"
    receiving = "targets" if binding == "renamed" else "places"
    data = Dependency("rows", producer=producer, parameters={"weights": [3]})
    if binding != "matching":
        data = data.bind(**{receiving: Input("places")})

    @metric(
        name="summary",
        description="Keep the original target context complete",
        code_revision="1",
        inputs={
            "places": LocationDependency(provider="target"),
            "data": Dependency(
                "rows", producer="producer", parameters={"weights": [5]}
            ),
        },
        outputs={"total": RESULT},
    )
    def summary(places: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        trace.capture("summary", places)
        assert set(data.location_id) == {"X", "Y", "Z"}
        return pd.DataFrame({"total": [int(data.value.sum())]})

    @metric(
        name="selection",
        description="Compare associated targets against an independent selection",
        code_revision="1",
        inputs={
            "places": LocationDependency(provider="source", primary=True),
            "reference": LocationDependency(provider="source"),
            "data": data,
            "references": Dependency(
                "rows", producer="producer", parameters={"weights": [4]}
            ).bind(places=Input("reference")),
            "total": Dependency("total", producer="summary"),
        },
        outputs={"rows": schema()},
    )
    def selection(
        places: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        data: pd.DataFrame,
        references: pd.DataFrame,
        total: pd.DataFrame,
    ) -> pd.DataFrame:
        assert set(places.location_id) == {"b"}
        assert set(reference.location_id) == {"a", "b"}
        assert set(data.location_id) == {"Y", "Z"}
        assert data.value.tolist() == [30, 30]
        assert set(references.location_id) == {"X", "Y", "Z"}
        assert references.value.tolist() == [40, 40, 40]
        assert total.total.tolist() == [150]
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": len(data)}
        )

    plugins["selection"] = PluginDefinition(metrics=[selection, summary])
    runner = LocalRunner.from_definitions(plugins)
    plan = prepare_execution(
        runner.catalog,
        {
            "metric": "selection",
            "inputs": {
                "places": {"provider": "source", "ids": ["a", "b"]},
                "reference": {"provider": "source", "ids": ["a", "b"]},
            },
        },
    )
    executor = GraphExecutor(runner.catalog, Mock())
    bound = executor.validate_plan(plan)
    # Inject demand at the existing native boundary; no persistent scheduler.
    frame = gpd.GeoDataFrame(
        {"location_id": ["a", "b"], "value": [1, 1]},
        geometry=gpd.points_from_xy([0, 0], [0, 0]),
        crs=4326,
    )
    contract = runner.catalog.provider_registry["source"].contract.reference
    original = runner.catalog.implementations["selection"].prepare_resolved_invocation(
        "selection",
        {
            "places": NativeLocationInput(frame, contract),
            "reference": NativeLocationInput(frame, contract),
        },
    )
    original = replace(original, parameters=None, bound_use=bound)
    reduced = original.select_primary("places", frozenset(demand))
    outcome = executor.execute(reduced)
    assert outcome.outputs["rows"].value.tolist() == ([2] if demand else [])
    assert set(trace.frames["summary"][0].location_id) == {"X", "Y", "Z"}
    observed = {
        tuple(values): set(frame.location_id)
        for (_, values), frame in zip(
            [item for item in trace.parameters if item[0] == "measure"],
            trace.frames["measure"],
            strict=True,
        )
    }
    expected = {"Y", "Z"} if policy == "per_location" else {"X", "Y", "Z"}
    if demand or policy != "per_location":
        assert observed[3,] == expected
    else:
        assert (3,) not in observed
    assert observed[4,] == {"X", "Y", "Z"}
    assert observed[5,] == {"X", "Y", "Z"}
    for completion in outcome.completions:
        if (
            completion.provider_scope is not None
            and completion.provider_scope.provider_id == "target"
        ):
            assert completion.partitions <= {"X", "Y", "Z"}
    for frame in trace.frames["second"]:
        assert set(zip(frame.source_location_id, frame.location_id, strict=True)) == {
            ("a", "x"),
            ("a", "y"),
            ("b", "y"),
            ("b", "z"),
        }
