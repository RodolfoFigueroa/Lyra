"""Complete plans fail before acquisition and successful cache reuse."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import metric
from test_support.canonical_execution import canonical_workload
from tests.graph_execution_helpers import TABLE, executor


@pytest.mark.parametrize(
    "damage", ["implementation", "root", "outputs", "inputs", "route", "scope"]
)
def test_prepared_rejection_clears_success_before_acquisition(
    damage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = canonical_workload("routed", 1, 1)
    runner, plan = work.executor, work.prepared
    runner.cache = TableCache(tmp_path)
    direct = prepare_execution(
        runner.catalog,
        {
            "metric": "one",
            "inputs": {"left": {"provider": "bench_target", "ids": ["0"]}},
        },
    )
    assert runner.execute(direct).provenance.execution.succeeded
    item = plan.inputs[0]
    if damage == "implementation":
        runner.plugins = {}
    elif damage == "root":
        plan = plan.model_copy(update={"node": "missing"})
    elif damage == "outputs":
        plan = plan.model_copy(
            update={"outputs": {"unknown": next(iter(plan.outputs.values()))}}
        )
    elif damage == "inputs":
        plan = plan.model_copy(update={"inputs": ()})
    elif damage == "route":
        plan = plan.model_copy(
            update={
                "inputs": (replace(item, binding=replace(item.binding, operations=())),)
            }
        )
    else:
        plan = plan.model_copy(
            update={"revisions": replace(plan.revisions, calculation=frozenset())}
        )
    acquire = Mock(side_effect=AssertionError("acquired resources"))
    cache_read = Mock(side_effect=AssertionError("reused cache"))
    monkeypatch.setattr(runner, "_collect_resources", acquire)
    monkeypatch.setattr(runner.cache, "read", cache_read)
    with pytest.raises(GraphExecutionError):
        runner.execute(plan)
    acquire.assert_not_called()
    cache_read.assert_not_called()
    assert runner.provenance is not None
    assert not runner.provenance.execution.succeeded
    assert runner.provenance.execution.observations == ()
    assert runner.provenance.execution.native_inputs == ()


@pytest.mark.parametrize(
    "damage", ["implementation", "output", "binding", "root", "inputs"]
)
def test_resolved_validation_precedes_cached_root(
    damage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    @metric(
        code_revision="1", name="leaf", description="Leaf", outputs={"table": TABLE}
    )
    def leaf() -> pd.DataFrame:
        calls.append("leaf")
        return pd.DataFrame()

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"table": TABLE},
        inputs={"table": Dependency(producer="leaf", output="table")},
    )
    def root(table: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return table

    runner, plugin = executor(root, leaf)
    runner.cache = TableCache(tmp_path)
    invocation = plugin.prepare_resolved_invocation("root", {})
    runner.execute(invocation)
    assert calls == ["leaf", "root"]
    if damage == "implementation":
        runner.plugins = {"plugin_0": plugin}
    elif damage == "output":
        edge = runner.graph.dependencies["root"]["table"]
        runner.graph.dependencies["root"]["table"] = replace(
            edge, dependency=Dependency(producer="leaf", output="unknown")
        )
    elif damage == "binding":
        runner.graph.dependencies["root"].clear()
    elif damage == "root":
        invocation = replace(invocation, node="unknown", parameters=None)
    else:
        invocation.__dict__["locations"] = {"unknown": None}
    acquire = Mock(side_effect=AssertionError("acquired resources"))
    monkeypatch.setattr(runner, "_collect_resources", acquire)
    with pytest.raises(GraphExecutionError):
        runner.execute(invocation)
    acquire.assert_not_called()
    assert calls == ["leaf", "root"]
    assert runner.provenance is not None
    assert runner.provenance.execution.observations == ()
