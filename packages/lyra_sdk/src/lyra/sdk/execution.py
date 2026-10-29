"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.sdk.arguments import prepare_dependency
from lyra.sdk.resources import BaseIO, ResourceConfig, ResourceScope, collect_resources

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
    context factory supplies the current node's identity, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    """

    def __init__(
        self,
        graph: PluginGraph,
        plugins: Mapping[str, PluginDefinition],
        context_factory: Callable[[str], RunContext],
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO]] | None = None,
    ) -> None:
        """Bind a validated graph to implementations and operational services."""
        self.graph = graph
        self.plugins = plugins
        self.context_factory = context_factory
        self.resource_config = resource_config
        self.resource_overrides = resource_overrides
        self.resource_providers = collect_resources(plugins)

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
            with ResourceScope(
                self.resource_providers, self.resource_config, self.resource_overrides
            ).activate():
                return self._execute(root, (root.node,), completed)
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(root.node, (root.node,), exc) from exc
        finally:
            # Discard the lookup even when a retained traceback outlives the job.
            completed.clear()

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
