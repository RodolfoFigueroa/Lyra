"""Selected execution ordering is checked before enqueueing or acquisition."""

import asyncio
from collections.abc import Callable, Iterator
from graphlib import TopologicalSorter
from typing import Any
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from fastapi import HTTPException

from lyra.contracts.accepted import PreparedInput
from lyra.contracts.dependencies import Dependency, LocationDependency
from lyra.contracts.jobs import JobCreateRequest
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.registry import Catalog
from lyra.server.routes.jobs import create_job
from tests.live_graph_helpers import operation
from tests.node_declaration_helpers import provider


def request(**inputs: str) -> dict[str, Any]:
    return {
        "metric": "root",
        "inputs": {
            key: {"provider": value, "ids": ["one"]} for key, value in inputs.items()
        },
    }


def interacting_definitions(*, nested: bool = False) -> dict[str, PluginDefinition]:
    @metric(
        name="root",
        code_revision="1",
        description="Combine ring routes.",
        inputs={
            "left": LocationDependency(provider="d"),
            "right": LocationDependency(provider="b"),
        },
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )
    def root(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
        del left, right
        pytest.fail("Planning invoked the root")

    metrics: list[Callable] = [root]
    if nested:

        @metric(
            name="wrapper",
            code_revision="1",
            description="Supply both receiving routes.",
            inputs={
                "left": LocationDependency(provider="a"),
                "right": LocationDependency(provider="c"),
                "data": Dependency("value", producer="root"),
            },
            outputs={"value": pa.DataFrameSchema(strict=True)},
        )
        def wrapper(
            left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, data: pd.DataFrame
        ) -> pd.DataFrame:
            del left, right, data
            pytest.fail("Planning invoked the wrapper")

        metrics.append(wrapper)
    return {
        "ring": PluginDefinition(
            metrics=metrics,
            resources={key: provider(id=key) for key in "abcd"},
            location_operations=[
                operation(a + b, a, b) for a, b in zip("abcd", "bcda", strict=True)
            ],
        )
    }


def test_interacting_routes_and_exclusive_alternatives() -> None:
    catalog = compile_live_catalog(interacting_definitions())
    for left, right in [("a", "b"), ("d", "c"), ("d", "b")]:
        assert prepare_execution(catalog, request(left=left, right=right))
    with pytest.raises(
        PreparationError, match=r"Execution plan cycle.*root.*left.*right"
    ):
        prepare_execution(catalog, request(left="a", right="c"))


def test_interacting_routes_below_the_root_are_validated_together() -> None:
    catalog = compile_live_catalog(interacting_definitions(nested=True))
    with pytest.raises(
        PreparationError, match=r"Execution plan cycle.*root.*left.*right"
    ):
        prepare_execution(catalog, request(left="a", right="c") | {"metric": "wrapper"})


def test_submission_rejects_cycle_without_enqueueing() -> None:
    catalog = compile_live_catalog(interacting_definitions())
    jobs = Mock()
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_job(
                JobCreateRequest.model_validate(request(left="a", right="c")),
                catalog=Catalog(
                    catalog,
                    {
                        name: "default"
                        for name, entry in catalog.graph.nodes.items()
                        if entry.node.kind == "metric"
                    },
                ),
                jobs=jobs,
            )
        )
    assert error.value.status_code == 422
    assert "Execution plan cycle" in str(error.value.detail)
    jobs.enqueue.assert_not_called()


def test_local_rejection_precedes_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = LocalRunner.from_definitions(interacting_definitions())
    execute = Mock(side_effect=AssertionError("execution started"))
    monkeypatch.setattr(LocalRunner, "_execute", execute)
    with pytest.raises(PreparationError, match="Execution plan cycle"):
        runner.run("root", inputs=request(left="a", right="c")["inputs"])
    execute.assert_not_called()


@pytest.mark.parametrize("boundary", ["validate_plan", "acquire_sources", "execute"])
def test_captured_cycle_rejected_before_resources(
    boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = compile_live_catalog(interacting_definitions())
    valid = prepare_execution(catalog, request(left="d", right="b"))
    # Exercise the executor's defensive boundary with an otherwise bypassed plan.
    inputs = tuple(
        PreparedInput(
            name=name,
            ids=("one",),
            binding=catalog.accepted_sources["root"][name][value],
        )
        for name, value in (("left", "a"), ("right", "c"))
    )
    invalid = valid.model_copy(update={"inputs": inputs})
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    executor = GraphExecutor(catalog, Mock())
    expected = GraphExecutionError if boundary == "execute" else ValueError
    with pytest.raises(expected, match="Execution plan cycle"):
        getattr(executor, boundary)(invalid)
    acquire.assert_not_called()


def test_startup_work_does_not_multiply_input_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_count = 4
    native = "native"
    sources = [f"source_{i}" for i in range(3)]

    def four(
        input_0: gpd.GeoDataFrame,
        input_1: gpd.GeoDataFrame,
        input_2: gpd.GeoDataFrame,
        input_3: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        del input_0, input_1, input_2, input_3
        pytest.fail("Planning invoked a metric")

    root = metric(
        name="root",
        description="Many independent inputs",
        code_revision="1",
        inputs={
            f"input_{i}": LocationDependency(provider=native)
            for i in range(input_count)
        },
        outputs={"value": pa.DataFrameSchema(strict=True)},
    )(four)
    operations = [
        operation(f"select_{i}", source, native) for i, source in enumerate(sources)
    ]
    definitions = {
        "test": PluginDefinition(
            metrics=[root],
            location_operations=operations,
            resources={name: provider(id=name) for name in [native, *sources]},
        )
    }
    original_order = TopologicalSorter.static_order
    visited = 0
    # Startup may inspect individual choices, but must not sort a plan for every
    # combination (4**input_count). Allow a full graph traversal per choice,
    # independent of how those visits are divided into sorting calls.
    budget = (1 + len(operations)) * input_count * (1 + len(sources))

    def static_order(sorter: TopologicalSorter) -> Iterator[str]:
        nonlocal visited
        for node in original_order(sorter):
            visited += 1
            assert visited <= budget, "Startup sorting exceeds per-choice work bound"
            yield node

    with monkeypatch.context() as startup:
        startup.setattr(TopologicalSorter, "static_order", static_order)
        catalog = compile_live_catalog(definitions)
    assert len(catalog.accepted_sources["root"]) == input_count
    assert all(
        len(choices) == 4 for choices in catalog.accepted_sources["root"].values()
    )
    assert prepare_execution(
        catalog, request(**{f"input_{i}": sources[i % 3] for i in range(input_count)})
    )


@pytest.mark.parametrize("missing", ["root", "ab"])
def test_unavailable_captured_node_precedes_acquisition(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = compile_live_catalog(interacting_definitions())
    prepared = prepare_execution(catalog, request(left="a", right="b"))
    del catalog.graph.nodes[missing]
    acquire = Mock(side_effect=AssertionError("resources acquired"))
    monkeypatch.setattr("lyra.runtime.resources.ResourceScope.get", acquire)
    executor = GraphExecutor(catalog, Mock())
    with pytest.raises(ValueError, match=f"unavailable node {missing!r}"):
        executor.acquire_sources(prepared)
    acquire.assert_not_called()
