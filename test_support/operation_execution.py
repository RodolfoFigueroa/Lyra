"""Expose operation dispatch for tests before correspondence consumers run."""

from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor, RunState, invocation_boundary
from lyra.runtime.operations import OperationResult


class OperationExecutor(GraphExecutor):
    """Exercise the production dispatch without changing public metric roots."""

    def dispatch(
        self,
        invocation: PreparedResolvedInvocation,
        state: RunState,
    ) -> OperationResult:
        """Execute the real operation branch with the caller's run-owned state."""
        path = (invocation.node,)
        with invocation_boundary(invocation.node, path, kind="location_operation"):
            return self._operation(invocation, state, path, selections=())
