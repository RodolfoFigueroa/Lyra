"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lyra.sdk.arguments import prepare_dependency
from lyra.sdk.errors import MetricResultError
from lyra.sdk.resources import BaseIO, ResourceConfig, ResourceScope, collect_resources

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.sdk.arguments import PreparedInvocation
    from lyra.sdk.cache import TableCache
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


@dataclass
class GraphExecutor:
    """Execute graphs using registered implementations and job-scoped services.

    Plugins are keyed by the distribution names used to build the graph. The
    context factory supplies the current node's identity, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    """

    graph: PluginGraph
    plugins: Mapping[str, PluginDefinition]
    context_factory: Callable[[str], RunContext]
    resource_config: Mapping[str, ResourceConfig] | None = None
    resource_overrides: Mapping[str, type[BaseIO]] | None = None
    cache: TableCache | None = field(default=None, kw_only=True)

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
                collect_resources(self.plugins),
                self.resource_config,
                self.resource_overrides,
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
        if self.cache is not None:
            cache_key = self.cache.key(key)
            cached = self._cached(invocation, cache_key)
            if cached is not None:
                completed[key] = cached
                return cached
            with self.cache.lock(cache_key) as acquired:
                if acquired:
                    cached = self._cached(invocation, cache_key)
                    if cached is not None:
                        completed[key] = cached
                        return cached
                outputs = self._calculate(invocation, path, completed)
                if acquired:
                    try:
                        self.cache.write(cache_key, outputs)
                    except (ValueError, TypeError, NotImplementedError) as exc:
                        raise MetricResultError(
                            invocation.node,
                            "outputs",
                            f"Table storage serialization failed: {exc}",
                        ) from exc
                return outputs
        return self._calculate(invocation, path, completed)

    def _cached(self, invocation: PreparedInvocation, key: str) -> NativeOutputs | None:
        if self.cache is None:
            return None
        outputs = self.cache.read(key)
        entry = self.graph.nodes[invocation.node]
        if outputs is None or outputs.keys() != entry.node.outputs.keys():
            return None
        value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
        try:
            return self.plugins[entry.distribution].normalize_result(
                invocation.node, value
            )
        except MetricResultError:
            return None

    def _calculate(
        self,
        invocation: PreparedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs],
    ) -> NativeOutputs:
        key = invocation.identity
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
