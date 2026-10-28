"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.sdk.arguments import prepare_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Literal

    from lyra.sdk.arguments import PreparedInvocation
    from lyra.sdk.context import RunContext
    from lyra.sdk.graph import PluginGraph
    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.results import NativeOutputs


class GraphExecutionError(RuntimeError):
    """A failed invocation with its root-to-node dependency path and cause."""

    def __init__(self, node: str, path: tuple[str, ...], cause: Exception) -> None:
        """Record the failing node and traversal path for worker diagnostics."""
        self.node = node
        self.path = path
        super().__init__(f"Node {node!r}, path {' -> '.join(path)}: {cause}")


class GraphExecutor:
    """Execute graphs using registered implementations and job-scoped services.

    Plugins are keyed by the distribution names used to build the graph. The
    context factory supplies the current node's identity and role, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    """

    def __init__(
        self,
        graph: PluginGraph,
        plugins: Mapping[str, PluginDefinition],
        context_factory: Callable[[str, Literal["metric", "dataset"]], RunContext],
    ) -> None:
        """Bind a validated graph to implementations and operational services."""
        self.graph = graph
        self.plugins = plugins
        self.context_factory = context_factory

    def execute(self, root: PreparedInvocation) -> NativeOutputs:
        """Run one prepared public metric and return all its native outputs.

        Returns:
            Only the requested metric's named tables.

        Raises:
            GraphExecutionError: On the first preparation, execution, or result
                failure, chained to its underlying exception.
        """
        completed: dict[str, NativeOutputs] = {}
        try:
            self._check_root(root)
            return self._execute(root, (root.node,), completed)
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(root.node, (root.node,), exc) from exc
        finally:
            # Discard the lookup even when a retained traceback outlives the job.
            completed.clear()

    def _check_root(self, root: PreparedInvocation) -> None:
        if self.graph.nodes[root.node].node.role != "metric":
            msg = "execution root must be a public metric"
            raise ValueError(msg)

    def _execute(
        self,
        invocation: PreparedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs],
    ) -> NativeOutputs:
        try:
            return self._invoke(invocation, path, completed)
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(invocation.node, path, exc) from exc

    def _invoke(
        self,
        invocation: PreparedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs],
    ) -> NativeOutputs:
        key = invocation.identity
        if key in completed:
            return completed[key]
        dependencies: dict[str, object] = {}
        for name, edge in self.graph.dependencies[invocation.node].items():
            producer = edge.output.producer
            dependency_path = (*path, producer.node.name)
            try:
                prepared = prepare_dependency(
                    invocation, edge, self.plugins[producer.distribution]
                )
            except Exception as exc:
                raise GraphExecutionError(
                    producer.node.name, dependency_path, exc
                ) from exc
            outputs = self._execute(prepared, dependency_path, completed)
            dependencies[name] = outputs[edge.dependency.output]
        plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
        outputs = plugin.invoke(invocation, dependencies, self.context_factory)
        completed[key] = outputs
        return outputs
