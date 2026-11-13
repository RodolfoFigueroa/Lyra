"""Expose operation dispatch for tests before correspondence consumers run."""

from collections.abc import Callable

from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor, RunState
from lyra.runtime.operations import OperationResult
from lyra.sdk.context import RunContext


class OperationExecutor(GraphExecutor):
    """Exercise the production dispatch without changing public metric roots."""

    def dispatch(
        self,
        invocation: PreparedResolvedInvocation,
        state: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> OperationResult:
        """Execute the real operation branch with the caller's run-owned state."""
        self._execute(invocation, (invocation.node,), state, context_factory)
        value = state[invocation.identity]
        assert isinstance(value, OperationResult)
        return value
