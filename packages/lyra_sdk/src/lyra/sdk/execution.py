"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from lyra.sdk.arguments import (
    PreparedInvocation,
    PreparedResolvedInvocation,
    PreparedSelectionInvocation,
    prepare_dependency,
)
from lyra.sdk.context import LocationContextAdapter
from lyra.sdk.graph import ResolvedDependency, ResolvedLocationDependency
from lyra.sdk.location_resolution import LocationResolver
from lyra.sdk.locations import LocationProvider
from lyra.sdk.models.location import Locations
from lyra.sdk.resources import BaseIO, ResourceConfig, ResourceScope, collect_resources

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from lyra.sdk.cache import TableCache
    from lyra.sdk.context import RunContext
    from lyra.sdk.dependencies import LocationDependencyManifest
    from lyra.sdk.graph import PluginGraph
    from lyra.sdk.locations import SelectedLocationProvider
    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.results import NativeOutputs


logger = logging.getLogger(__name__)


class GraphExecutionError(RuntimeError):
    """A failed invocation with its root-to-node dependency path and cause."""

    def __init__(
        self,
        node: str,
        path: tuple[str, ...],
        cause: Exception,
        *,
        receiving_input: str | None = None,
        kind: str = "metric",
    ) -> None:
        """Record the failing node and traversal path for worker diagnostics."""
        self.kind = kind
        self.node = node
        self.path = path
        self.receiving_input = receiving_input
        receiving = f", receiving input {receiving_input!r}" if receiving_input else ""
        super().__init__(
            f"Node {node!r} ({kind}), path {' -> '.join(path)}{receiving}: {cause}"
        )


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

    location_provider: SelectedLocationProvider | None = field(
        default=None, kw_only=True
    )

    def execute(
        self,
        root: PreparedInvocation
        | PreparedResolvedInvocation
        | PreparedSelectionInvocation,
    ) -> NativeOutputs:
        """Run one prepared public metric and return all its native outputs.

        Returns:
            Only the requested metric's named tables.

        Raises:
            GraphExecutionError: On the first preparation, execution, or result
                failure, chained to its underlying exception.
        """
        if self.graph.nodes[root.node].node.kind != "metric":
            cause = ValueError("Public execution roots must be metrics")
            raise GraphExecutionError(
                root.node,
                (root.node,),
                cause,
                kind=self.graph.nodes[root.node].node.kind,
            ) from cause
        completed: dict[str, NativeOutputs | Locations] = {}
        try:
            resources = collect_resources(self.plugins)
            scope = ResourceScope(
                resources, self.resource_config, self.resource_overrides
            )
            with scope.activate():
                return self._execute_scoped(root, scope, completed)
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(root.node, (root.node,), exc) from exc
        finally:
            # Discard the lookup even when a retained traceback outlives the job.
            completed.clear()

    def _execute_scoped(
        self,
        root: PreparedInvocation
        | PreparedResolvedInvocation
        | PreparedSelectionInvocation,
        scope: ResourceScope,
        completed: dict[str, NativeOutputs | Locations],
    ) -> NativeOutputs:
        resources = collect_resources(self.plugins)
        declaration = (
            resources.get(self.location_provider.resource)
            if self.location_provider is not None
            else None
        )
        resolver = LocationResolver(
            scope,
            selected=self.location_provider,
            provider=declaration
            if declaration is not None and issubclass(declaration, LocationProvider)
            else None,
        )
        if isinstance(root, PreparedSelectionInvocation):
            locations = resolver.resolve(root.selections, node=root.node)
            root = PreparedResolvedInvocation(root.node, root.parameters, locations)
        elif isinstance(root, PreparedInvocation):
            root = PreparedResolvedInvocation(
                root.node, root.parameters, root.spatial, root.kind
            )

        def context_factory(node: str) -> RunContext:
            return LocationContextAdapter(self.context_factory(node), resolver)

        result = self._execute(root, (root.node,), completed, context_factory)
        return cast("NativeOutputs", result)

    def _execute(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs | Locations],
        context_factory: Callable[[str], RunContext],
        receiving_input: str | None = None,
    ) -> NativeOutputs | Locations:
        try:
            return self._invoke(invocation, path, completed, context_factory)
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(
                invocation.node,
                path,
                exc,
                receiving_input=receiving_input,
                kind=invocation.kind,
            ) from exc

    def _invoke(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs | Locations],
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs | Locations:
        key = invocation.identity
        if key in completed:
            return completed[key]
        cache_key = self.cache.key(key) if self.cache is not None else None
        if cache_key is not None:
            cached = self._cached(invocation, cache_key)
            if cached is not None:
                completed[key] = cached
                return cached

        # Recursive work must finish before holding this invocation's lock.
        dependencies = self._dependencies(invocation, path, completed, context_factory)
        with self._cache_lock(cache_key) as acquired:
            if acquired and cache_key is not None:
                cached = self._cached(invocation, cache_key)
                if cached is not None:
                    completed[key] = cached
                    return cached
            plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
            outputs = (
                plugin.invoke_operation(invocation, dependencies, context_factory)
                if invocation.kind == "location_operation"
                else plugin.invoke(invocation, dependencies, context_factory)
            )
            completed[key] = outputs
            if acquired and self.cache is not None and cache_key is not None:
                try:
                    if isinstance(outputs, Locations):
                        self.cache.write_locations(cache_key, outputs)
                    else:
                        self.cache.write(cache_key, outputs)
                except Exception:
                    logger.warning(
                        "Cache publication failed: %s", cache_key, exc_info=True
                    )
            return outputs

    @contextmanager
    def _cache_lock(self, key: str | None) -> Iterator[bool]:
        # Catch only storage entry/exit failures; handler exceptions must propagate.
        stack = ExitStack()
        acquired = False
        try:
            if self.cache is not None and key is not None:
                acquired = stack.enter_context(self.cache.lock(key))
        except Exception:
            logger.warning("Cache lock unavailable: %s", key, exc_info=True)
        try:
            yield acquired
        finally:
            try:
                stack.close()
            except Exception:
                logger.warning("Cache unlock failed: %s", key, exc_info=True)

    def _cached(
        self, invocation: PreparedResolvedInvocation, key: str
    ) -> NativeOutputs | Locations | None:
        try:
            return self._read_cached(invocation, key)
        except Exception:
            logger.warning("Unusable cache entry: %s", key, exc_info=True)
            return None

    def _read_cached(
        self, invocation: PreparedResolvedInvocation, key: str
    ) -> NativeOutputs | Locations | None:
        if self.cache is None:
            return None
        entry = self.graph.nodes[invocation.node]
        if invocation.kind == "location_operation":
            locations = self.cache.read_locations(key)
            if locations is None:
                return None
            return self.plugins[entry.distribution].normalize_location_result(
                invocation.node, locations
            )
        outputs = self.cache.read(key)
        if outputs is None or outputs.keys() != entry.node.outputs.keys():
            return None
        value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
        return self.plugins[entry.distribution].normalize_result(invocation.node, value)

    def _dependencies(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs | Locations],
        context_factory: Callable[[str], RunContext],
    ) -> dict[str, object]:
        dependencies: dict[str, object] = {}
        for name, edge in self.graph.dependencies[invocation.node].items():
            producer = edge.output.producer
            dependency_path = (*path, producer.node.name)
            prepared = self._prepare(
                invocation, edge, dependency_path, completed, context_factory
            )
            outputs = self._execute(
                prepared,
                dependency_path,
                completed,
                context_factory,
                receiving_input=f"inputs.{name}",
            )
            dependencies[name] = cast("NativeOutputs", outputs)[edge.dependency.output]
        return dependencies

    def _prepare(
        self,
        owner: PreparedResolvedInvocation,
        edge: ResolvedDependency | ResolvedLocationDependency,
        path: tuple[str, ...],
        completed: dict[str, NativeOutputs | Locations],
        context_factory: Callable[[str], RunContext],
    ) -> PreparedResolvedInvocation:
        producer = (
            edge.output.producer
            if isinstance(edge, ResolvedDependency)
            else edge.producer
        )
        binding_path = (
            f"inputs.{edge.input_name}"
            if isinstance(edge, ResolvedDependency)
            else edge.binding_path
        )

        def location_result(
            name: str, binding: LocationDependencyManifest
        ) -> Locations:
            nested = self.graph.location_dependencies[owner.node][
                f"{binding_path}.locations.{name}"
            ]
            nested_path = (*path, binding.operation)
            prepared = self._prepare(
                owner, nested, nested_path, completed, context_factory
            )
            result = self._execute(
                prepared,
                nested_path,
                completed,
                context_factory,
                receiving_input=nested.binding_path,
            )
            return cast("Locations", result)

        try:
            return prepare_dependency(
                owner,
                edge,
                self.plugins[producer.distribution],
                location_result=location_result,
            )
        except GraphExecutionError:
            raise
        except Exception as exc:
            raise GraphExecutionError(
                producer.node.name,
                path,
                exc,
                receiving_input=binding_path,
                kind=producer.node.kind,
            ) from exc
