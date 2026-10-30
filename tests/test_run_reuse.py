"""Run-owned preparation reuse and cleanup."""

import gc
import weakref
from collections import Counter
from typing import Never
from unittest.mock import patch

import pandas as pd
import pytest
from lyra.sdk import Dependency, metric
from lyra.sdk.arguments import prepare_dependency
from lyra.sdk.execution import GraphExecutionError, RunState

from tests.test_graph_execution import TABLE, executor


@pytest.mark.parametrize("fail", [False, True])
def test_diamond_preparation_and_run_cleanup(*, fail: bool) -> None:
    calls: Counter[str] = Counter()

    @metric(name="leaf", description="Leaf", outputs={"leaf": TABLE})
    def leaf() -> pd.DataFrame:
        calls["leaf"] += 1
        return pd.DataFrame(index=[0])

    @metric(
        name="branch",
        description="Branch",
        outputs={"branch": TABLE},
        inputs={"table": Dependency("leaf")},
    )
    def branch(table: pd.DataFrame) -> pd.DataFrame:
        calls["branch"] += 1
        return table

    @metric(
        name="root",
        description="Root",
        outputs={"root": TABLE},
        inputs={"left": Dependency("branch"), "right": Dependency("branch")},
    )
    def root(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        calls["root"] += 1
        if fail:
            message = "handler failure"
            raise ValueError(message)
        return left + right

    runner, plugin = executor(root, branch, leaf)
    invocation = plugin.prepare_invocation("root", {})
    states: list[RunState] = []
    original = RunState.clear

    def clear(state: RunState) -> None:
        states.append(state)
        original(state)

    with (
        patch(
            "lyra.sdk.execution.prepare_dependency", wraps=prepare_dependency
        ) as prepare,
        patch.object(RunState, "clear", clear),
    ):
        for _ in range(2):
            if fail:
                with pytest.raises(GraphExecutionError) as error:
                    runner.execute(invocation)
                assert error.value.__cause__ is not None
                assert error.value.__cause__.__traceback__ is not None
                assert error.value.__cause__.__traceback__.tb_frame.f_locals == {}
            else:
                assert len(runner.execute(invocation)["root"]) == 1
        assert prepare.call_count == 4
    assert calls == {"root": 2, "branch": 2, "leaf": 2}
    assert len(states) == 2
    assert states[0] is not states[1]
    assert all(not state and not state.prepared for state in states)


@pytest.mark.parametrize("grouped", [False, True])
def test_retained_exception_releases_handler_locals(*, grouped: bool) -> None:
    references = []

    def fail() -> Never:
        table = pd.DataFrame(index=[0])
        references.append(weakref.ref(table))
        message = "inner failure"
        raise ValueError(message)

    @metric(name="root", description="Root", outputs={"root": TABLE})
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
            runner.execute(plugin.prepare_invocation("root", {}))
        gc.collect()
        assert retained.value.__cause__ is not None
        assert all(reference() is None for reference in references)
