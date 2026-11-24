"""Run-owned preparation reuse and cleanup."""

import gc
import weakref
from collections import Counter
from typing import Never
from unittest.mock import patch

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.runtime.execution import GraphExecutionError, RunState
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import metric
from tests.graph_execution_helpers import TABLE, executor


@pytest.mark.parametrize("fail", [False, True])
def test_diamond_shared_execution_and_run_cleanup(*, fail: bool) -> None:
    calls: Counter[str] = Counter()
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Run value", metadata={"unit": "count"})},
        strict=True,
    )

    @metric(
        code_revision="1", name="leaf", description="Leaf", outputs={"leaf": schema}
    )
    def leaf() -> pd.DataFrame:
        calls["leaf"] += 1
        return pd.DataFrame({"value": [calls["leaf"]]})

    @metric(
        code_revision="1",
        name="branch",
        description="Branch",
        outputs={"branch": schema},
        inputs={"table": Dependency("leaf", producer="leaf")},
    )
    def branch(table: pd.DataFrame) -> pd.DataFrame:
        calls["branch"] += 1
        return table

    @metric(
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": schema},
        inputs={
            "left": Dependency("branch", producer="branch"),
            "right": Dependency("branch", producer="branch"),
        },
    )
    def root(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        calls["root"] += 1
        if fail:
            message = "handler failure"
            raise ValueError(message)
        return left + right

    runner, plugin = executor(root, branch, leaf)
    invocation = plugin.prepare_resolved_invocation("root", {})
    states: list[RunState] = []
    original = RunState.clear

    def clear(state: RunState) -> None:
        states.append(state)
        original(state)

    # Each run owns its state and releases it on both success and failure.
    with patch.object(RunState, "clear", clear):
        for run in range(1, 3):
            if fail:
                with pytest.raises(GraphExecutionError) as error:
                    runner.execute(invocation)
                assert error.value.node == "root"
                assert error.value.__cause__ is not None
                assert error.value.__cause__.__traceback__ is not None
                assert error.value.__cause__.__traceback__.tb_frame.f_locals == {}
            else:
                result = runner.execute(invocation).outputs["root"]
                assert result.value.tolist() == [2 * run]
    assert calls == {"root": 2, "branch": 2, "leaf": 2}
    assert len(states) == 2
    assert states[0] is not states[1]
    assert all(not state and state.bound_root is None for state in states)


@pytest.mark.parametrize("grouped", [False, True])
def test_retained_exception_releases_handler_locals(*, grouped: bool) -> None:
    references = []

    def fail() -> Never:
        table = pd.DataFrame(index=[0])
        references.append(weakref.ref(table))
        message = "inner failure"
        raise ValueError(message)

    @metric(code_revision="1", name="root", description="Root", outputs={"root": TABLE})
    def root() -> pd.DataFrame:
        try:
            fail()
        except ValueError as exc:
            if grouped:
                message = "handler failures"
                raise ExceptionGroup(message, [exc]) from None
            message = "handler failure"
            raise RuntimeError(message) from None

    runner, plugin = executor(root)
    for _ in range(2):
        with pytest.raises(GraphExecutionError) as retained:
            runner.execute(plugin.prepare_resolved_invocation("root", {}))
        gc.collect()
        assert retained.value.__cause__ is not None
        assert all(reference() is None for reference in references)
