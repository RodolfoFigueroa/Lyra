"""Forwarding descriptions follow selected connections, independent of execution."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.dependencies import Dependency, Input
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.report_collection import ReportCollection
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.plugin import PluginDefinition, metric
from tests.bound_invocation_helpers import bound
from tests.report_parameter_helpers import (
    LeafParameters,
    MiddleParameters,
    RootParameters,
)
from tests.sqlite_helpers import database

__all__ = ["database"]


def test_direct_chains_shared_and_separate_uses_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = pa.DataFrameSchema(strict=True)
    handler = Mock(side_effect=AssertionError("capture executed a handler"))

    @metric(name="leaf", description="Leaf", outputs={"data": schema})
    def leaf(parameters: LeafParameters) -> pd.DataFrame:
        return handler(parameters)

    @metric(
        name="middle",
        description="Middle",
        outputs={"data": schema},
        inputs={
            "data": Dependency(
                "data",
                producer="leaf",
                parameters={
                    "distance": Input("parameters.radius"),
                },
            )
        },
    )
    def middle(data: pd.DataFrame, parameters: MiddleParameters) -> pd.DataFrame:
        return handler(data, parameters)

    @metric(
        name="root",
        description="Root",
        outputs={"data": schema},
        inputs={
            "first": Dependency(
                "data",
                producer="middle",
                parameters={
                    "radius": Input("parameters.search_radius"),
                },
            ),
            "second": Dependency(
                "data",
                producer="middle",
                parameters={
                    "radius": Input("parameters.alternate"),
                },
            ),
            "literal": Dependency(
                "data",
                producer="middle",
                parameters={
                    "radius": 5,
                    "options": {"items": [1, None]},
                    "label": None,
                    "tags": [],
                },
            ),
            "different": Dependency(
                "data", producer="middle", parameters={"radius": 7}
            ),
        },
    )
    def root(
        first: pd.DataFrame,
        second: pd.DataFrame,
        literal: pd.DataFrame,
        different: pd.DataFrame,
        parameters: RootParameters,
    ) -> pd.DataFrame:
        return handler(first, second, literal, different, parameters)

    catalog = compile_live_catalog(
        {"test": PluginDefinition(metrics=[root, middle, leaf])}
    )
    plan = prepare_execution(catalog, {"metric": "root"})
    invocation = bind_execution(catalog, plan)
    for plugin in catalog.implementations.values():
        monkeypatch.setattr(plugin, "prepare_parameters", handler)
    before = invocation.demand().computation_keys
    report = ReportCollection()
    report.capture(invocation, catalog, plan)
    first = invocation.dependencies["first"]
    shared = report.use_id(first)
    separate = report.use_id(invocation.dependencies["different"])
    root_id = report.use_id(invocation)
    assert shared == report.use_id(invocation.dependencies["second"])
    assert shared == report.use_id(invocation.dependencies["literal"])
    assert separate != shared
    connections = {
        (b.source_use_id, b.source_parameter, b.target_use_id, b.target_parameter)
        for b in report.parameter_bindings
    }
    assert connections == {
        (root_id, "search_radius", shared, "radius"),
        (root_id, "alternate", shared, "radius"),
        *(
            (root_id, name, target, name)
            for name in ("options", "tags", "label")
            for target in (shared, separate)
        ),
        (shared, "radius", report.use_id(first.dependencies["data"]), "distance"),
        (
            separate,
            "radius",
            report.use_id(invocation.dependencies["different"].dependencies["data"]),
            "distance",
        ),
    }
    assert len(report.parameter_bindings) == len(connections)
    assert report.request is not None
    assert (
        report.request.parameters
        == report.descriptions[root_id].parameters
        == plan.parameters
    )
    assert report.descriptions[shared].parameters == {
        "radius": 5,
        "options": {"items": [1, None]},
        "tags": [],
        "label": None,
        "mode": "default",
    }
    assert invocation.demand().computation_keys == before
    report.capture(invocation, catalog, plan)
    assert len(report.parameter_bindings) == len(connections)
    report.descriptions[shared].parameters["options"]["items"].append(9)
    assert first.parameter_values()["options"] == {"items": [1, None]}
    handler.assert_not_called()


@pytest.mark.parametrize("ids", [[], ["a", "b"]])
def test_nested_operation_forwarding_uses_declaring_caller(ids: list[str]) -> None:
    catalog, plan, root = bound(ids)
    report = ReportCollection()
    report.capture(root, catalog, plan)
    assert len(report.parameter_bindings) == 1
    connection = report.parameter_bindings[0]
    assert connection.source_use_id == report.root_use_id
    assert connection.source_parameter == connection.target_parameter == "year"
    assert report.uses[connection.target_use_id].node == "select"
    assert report.descriptions[connection.target_use_id].parameters == {"year": 2025}
    report.release_bindings()
    assert report.parameter_bindings == [connection]


@pytest.mark.usefixtures("database")
def test_cached_root_retains_forwarding_for_bypassed_dependency(tmp_path: Path) -> None:
    events = []
    schema = pa.DataFrameSchema(strict=True)

    @metric(name="leaf", description="Leaf", outputs={"data": schema})
    def leaf(parameters: LeafParameters) -> pd.DataFrame:
        events.append(parameters.distance)
        return pd.DataFrame()

    @metric(
        name="root",
        description="Root",
        cache="full",
        code_revision="1",
        outputs={"data": schema},
        inputs={
            "data": Dependency(
                "data",
                producer="leaf",
                parameters={
                    "distance": Input("parameters.search_radius"),
                },
            )
        },
    )
    def root(data: pd.DataFrame, parameters: RootParameters) -> pd.DataFrame:
        events.append(parameters.search_radius)
        return data

    catalog = compile_live_catalog({"test": PluginDefinition(metrics=[root, leaf])})
    plan = prepare_execution(catalog, {"metric": "root"})
    executor = GraphExecutor(
        catalog,
        catalog.implementations,
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    first = executor.execute(plan)
    assert events == [5, 5]
    events.clear()
    executor.context_factory = Mock(
        side_effect=AssertionError("cache hit acquired context")
    )
    again = executor.execute(plan)
    assert events == []
    for result in (first, again):
        report = result.report_state
        assert len(report.parameter_bindings) == 1
        binding = report.parameter_bindings[0]
        assert binding.source_use_id == report.root_use_id
        assert binding.source_parameter == "search_radius"
        assert binding.target_parameter == "distance"
        assert report.uses[binding.target_use_id].node == "leaf"
        assert report.descriptions[binding.target_use_id].parameters == {"distance": 5}
        assert report.request is not None
        assert report.request.parameters == plan.parameters
