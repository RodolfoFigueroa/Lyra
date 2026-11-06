"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

import json
import logging
import traceback
from collections import UserDict
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.provenance import (
    ExecutionObservations,
    ExecutionProvenance,
    InvocationObservation,
    NativeInputSummary,
)
from lyra.data.locations import Locations, location_fingerprint
from lyra.runtime.arguments import (
    PreparedResolvedInvocation,
    prepare_dependency,
)
from lyra.runtime.cache import CachedArtifact, TableCache
from lyra.runtime.context import LocationContextAdapter
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.computation import (
    executable_contract,
    validate_cache_revision,
)
from lyra.runtime.planning.graph import ResolvedDependency, ResolvedLocationDependency
from lyra.runtime.resources import ResourceScope, collect_resources
from lyra.sdk.locations import LocationProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.dependencies import LocationDependencyManifest
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.catalog import CompiledCatalog
    from lyra.runtime.planning.graph import PluginGraph
    from lyra.runtime.planning.providers import SelectedLocationProvider
    from lyra.sdk.context import RunContext
    from lyra.sdk.resources import BaseIO


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
        self.provenance: ExecutionProvenance | None = None
        self.kind = kind
        self.node = node
        self.path = path
        self.receiving_input = receiving_input
        receiving = f", receiving input {receiving_input!r}" if receiving_input else ""
        super().__init__(
            f"Node {node!r} ({kind}), path {' -> '.join(path)}{receiving}: {cause}"
        )


def _clear_exception_frames(error: BaseException) -> None:
    """Release locals throughout retained exception chains, including cycles."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


@dataclass(frozen=True)
class ExecutionOutcome:
    """Root tables, their retained artifact, and observations from one execution."""

    outputs: NativeOutputs
    artifact_id: str | None
    provenance: ExecutionProvenance


class RunState(UserDict[str, "NativeOutputs | Locations"]):
    """Own completed outputs and successful preparation for one execution."""

    def __init__(self) -> None:
        """Start an empty run."""
        super().__init__()
        self.observations: list[InvocationObservation] = []
        self.native_inputs: list[NativeInputSummary] = []
        self.prepared: dict[tuple[str, str, str], PreparedResolvedInvocation] = {}
        self.metadata: tuple[str, Mapping[str, str]] | None = None
        self.artifacts: dict[str, str | None] = {}
        self.root_artifact: str | None = None

    def clear(self) -> None:
        """Release every run-owned lookup."""
        super().clear()
        self.prepared.clear()
        self.artifacts.clear()
        self.metadata = None


@dataclass
class GraphExecutor:
    """Execute graphs using registered implementations and job-scoped services.

    Plugins are keyed by the distribution names used to build the graph. The
    context factory supplies the current node's identity, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    """

    catalog: CompiledCatalog
    plugins: Mapping[str, BoundPlugin]
    context_factory: Callable[[str], RunContext]
    resource_config: Mapping[str, ResourceConfig] | None = None
    resource_overrides: Mapping[str, type[BaseIO]] | None = None
    cache: TableCache | None = field(default=None, kw_only=True)

    cache_revision: str | None = field(default=None, kw_only=True)

    @property
    def graph(self) -> PluginGraph:
        """The captured execution graph."""
        return self.catalog.graph

    @property
    def location_provider(self) -> SelectedLocationProvider | None:
        """The captured provider declaration."""
        return self.catalog.location_provider

    def _collect_resources(self) -> dict[str, type[BaseIO]]:
        return collect_resources(
            {name: plugin.resources for name, plugin in self.plugins.items()}
        )

    def validate_plan(self, prepared: PreparedExecution) -> None:
        """Reject incompatible acceptance before acquiring any resource.

        Raises:
            ValueError: If accepted contracts, identities or revisions differ.
        """
        validate_execution_plan(
            self.graph,
            prepared.node,
            {item.name: item.binding.operations for item in prepared.inputs},
        )
        try:
            identity = self.catalog.computation.plan(prepared.node, prepared.inputs)
        except KeyError as exc:
            msg = "Accepted computation is unavailable"
            raise ValueError(msg) from exc
        if identity != prepared.computation_identity:
            msg = "Computation identity differs from accepted plan"
            raise ValueError(msg)
        for name, contract in prepared.contracts.nodes.items():
            entry = self.graph.nodes.get(name)
            binding = self.plugins.get(entry.distribution) if entry else None
            implemented = binding.node_manifest(name) if binding else None
            if implemented is None:
                msg = f"Accepted implementation is unavailable: {name}"
                raise ValueError(msg)
            if executable_contract(implemented) != executable_contract(contract):
                msg = f"Accepted implementation contract differs: {name}"
                raise ValueError(msg)
            for declaration in (
                *(item.contract for item in implemented.location_inputs.values()),
                *(
                    (implemented.output_contract,)
                    if implemented.kind == "location_operation"
                    else ()
                ),
            ):
                if (
                    self.catalog.revisions.contracts[declaration.reference].revision
                    != declaration.data_revision
                ):
                    msg = f"Cannot honor pinned implementation revision: {name}"
                    raise ValueError(msg)
        self._check_revisions(prepared)

    provenance: ExecutionProvenance | None = field(default=None, init=False)

    def acquire_sources(self, prepared: PreparedExecution) -> dict[str, Locations]:
        """Acquire prepared sources with executor resource ownership and cleanup.

        Operation declarations must still honor the accepted revisions. A changed
        declaration rejects execution; providers receive the pinned revision and
        may serve historical data or reject it. This does not execute routes.

        Returns:
            Validated source batches keyed by receiving input.
        """
        self.validate_plan(prepared)
        resources = self._collect_resources()
        declaration = (
            resources.get(self.location_provider.resource)
            if self.location_provider
            else None
        )
        scope = ResourceScope(resources, self.resource_config, self.resource_overrides)
        with scope.activate():
            resolver = LocationResolver(
                scope,
                selected=self.location_provider,
                provider=declaration
                if declaration is not None and issubclass(declaration, LocationProvider)
                else None,
            )
            return resolver.resolve_prepared(prepared)

    def _check_revisions(self, prepared: PreparedExecution) -> None:
        current = self.catalog.revisions
        operation_bindings = set(prepared.revisions.calculation)
        for item in prepared.inputs:
            for operation in item.binding.operations:
                operation_bindings.update(current.nodes[operation])
        pins = {binding.contract: binding for binding in prepared.revisions.bindings}
        for binding in operation_bindings:
            if current.contracts.get(binding.contract) != pins.get(binding.contract):
                message = (
                    f"Cannot honor pinned revision: {pins.get(binding.contract)!r}"
                )
                raise ValueError(message)

    def persistent_key(self, identity: str) -> str:
        """Select persistent storage independently of run-scoped identity.

        Callers must change cache_revision when external data or unversioned
        implementation changes affect results. Omission still permits reuse.

        Returns:
            The namespaced storage key.
        """
        namespace, revisions = self._cache_metadata()
        return self._metadata_key(identity, namespace, revisions)

    def _cache_metadata(self) -> tuple[str, Mapping[str, str]]:
        """Return compiled identities with this executor's revision override."""
        revision = validate_cache_revision(self.cache_revision)
        return json.dumps(revision), self.catalog.computation.cache_nodes

    @staticmethod
    def _metadata_key(identity: str, revision: str, nodes: Mapping[str, str]) -> str:
        node = json.loads(identity)["node"]
        return TableCache.key(nodes[node] + ":" + revision + ":" + identity)

    def _run_cache_key(self, identity: str, completed: RunState) -> str | None:
        """Return a storage key using the current run's captured metadata."""
        if self.cache is None:
            return None
        if completed.metadata is None:
            completed.metadata = self._cache_metadata()
        return self._metadata_key(identity, *completed.metadata)

    def execute(
        self,
        root: PreparedResolvedInvocation | PreparedExecution,
    ) -> ExecutionOutcome:
        """Run one prepared public metric and return all its native outputs.

        Returns:
            The requested metric's native tables, artifact reference, and provenance.

        Preparation, execution, and result failures raise GraphExecutionError,
        chained to the underlying exception.
        """
        self.provenance = None
        completed = RunState()
        succeeded = False
        failure: GraphExecutionError | None = None
        try:
            self._validate_execution(root)
            resources = self._collect_resources()
            scope = ResourceScope(
                resources, self.resource_config, self.resource_overrides
            )
            with scope.activate():
                result = self._execute_scoped(root, scope, completed)
        except Exception as exc:
            _clear_exception_frames(exc)
            if isinstance(exc, GraphExecutionError):
                failure = exc
                raise
            failure = GraphExecutionError(root.node, (root.node,), exc)
            raise failure from exc
        else:
            succeeded = True
        finally:
            self.provenance = ExecutionProvenance(
                root if isinstance(root, PreparedExecution) else None,
                ExecutionObservations(
                    tuple(completed.native_inputs),
                    tuple(completed.observations),
                    succeeded,
                ),
            )
            if failure is not None:
                failure.provenance = self.provenance
            # Discard the lookup even when a retained traceback outlives the job.
            completed.clear()
        return ExecutionOutcome(result, completed.root_artifact, self.provenance)

    def _validate_execution(
        self, root: PreparedResolvedInvocation | PreparedExecution
    ) -> None:
        self._validate_root(root.node)
        if isinstance(root, PreparedExecution):
            self.validate_plan(root)

    def _validate_root(self, node: str) -> None:
        """Reject operation roots before acquiring execution resources.

        Raises:
            GraphExecutionError: If the root is an operation.
        """
        if self.graph.nodes[node].node.kind != "metric":
            cause = ValueError("Public execution roots must be metrics")
            raise GraphExecutionError(
                node,
                (node,),
                cause,
                kind=self.graph.nodes[node].node.kind,
            ) from cause

    def _execute_scoped(
        self,
        root: PreparedResolvedInvocation | PreparedExecution,
        scope: ResourceScope,
        completed: RunState,
    ) -> NativeOutputs:
        resources = self._collect_resources()
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

        def context_factory(node: str) -> RunContext:
            return LocationContextAdapter(self.context_factory(node), resolver)

        if isinstance(root, PreparedExecution):
            if root.cache_revision != self.cache_revision:
                message = "Executor cache revision differs from prepared execution"
                raise ValueError(message)
            locations = resolver.resolve_prepared(root)
            for item in root.inputs:
                value = locations[item.name]
                for operation in item.binding.operations:
                    entry = self.graph.nodes[operation]
                    plugin = self.plugins[entry.distribution]
                    arguments: dict[str, object] = {
                        next(iter(entry.node.location_inputs)): value
                    }
                    if "parameters" in cast(
                        "dict", entry.node.request_schema["properties"]
                    ):
                        arguments["parameters"] = {}
                    hop = plugin.prepare_resolved_invocation(operation, arguments)
                    value = cast(
                        "Locations",
                        self._execute(
                            hop,
                            (root.node, operation),
                            completed,
                            context_factory,
                            receiving_input=item.name,
                        ),
                    )
                locations[item.name] = value
                completed.native_inputs.append(
                    NativeInputSummary(
                        item.name, len(value), location_fingerprint(value)
                    )
                )
            arguments = dict(locations)
            metric_arguments: dict[str, object] = dict(arguments)
            entry = self.graph.nodes[root.node]
            if "parameters" in cast("dict", entry.node.request_schema["properties"]):
                metric_arguments["parameters"] = root.parameters
            root = self.plugins[entry.distribution].prepare_resolved_invocation(
                root.node, metric_arguments
            )

        result = self._execute(root, (root.node,), completed, context_factory)
        completed.root_artifact = completed.artifacts.get(root.identity)
        return cast("NativeOutputs", result)

    def _execute(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
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
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs | Locations:
        key = invocation.identity
        if key in completed:
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "shared_reuse"
                )
            )
            return completed[key]
        cache_key = self._run_cache_key(key, completed)
        if cache_key is not None:
            cached = self._cached(invocation, cache_key)
            if cached is not None:
                completed.observations.append(
                    InvocationObservation(
                        invocation.node, TableCache.key(key), path, "cache_hit"
                    )
                )
                completed[key] = cached.value
                completed.artifacts[key] = cached.reference
                return cached.value

        # Recursive work must finish before holding this invocation's lock.
        dependencies = self._dependencies(invocation, path, completed, context_factory)
        with (
            self.cache.lock(cache_key)
            if self.cache is not None and cache_key is not None
            else nullcontext(enter_result=False)
        ) as acquired:
            if acquired and cache_key is not None:
                cached = self._cached(invocation, cache_key)
                if cached is not None:
                    completed.observations.append(
                        InvocationObservation(
                            invocation.node, TableCache.key(key), path, "cache_hit"
                        )
                    )
                    completed[key] = cached.value
                    completed.artifacts[key] = cached.reference
                    return cached.value
            plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "started"
                )
            )
            outputs = (
                plugin.invoke_operation(invocation, dependencies, context_factory)
                if invocation.kind == "location_operation"
                else plugin.invoke(invocation, dependencies, context_factory)
            )
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "completed"
                )
            )
            completed[key] = outputs
            if acquired and self.cache is not None and cache_key is not None:
                completed.artifacts[key] = (
                    self.cache.write_locations(cache_key, outputs)
                    if isinstance(outputs, Locations)
                    else self.cache.write(
                        cache_key, outputs, plugin.output_schemas(invocation.node)
                    )
                )
            return outputs

    def _cached(
        self, invocation: PreparedResolvedInvocation, key: str
    ) -> CachedArtifact[NativeOutputs] | CachedArtifact[Locations] | None:
        if self.cache is None:
            return None
        entry = self.graph.nodes[invocation.node]
        plugin = self.plugins[entry.distribution]
        if invocation.kind == "location_operation":
            return self.cache.read_locations(
                key, lambda value: plugin.validate_cached_locations(invocation, value)
            )

        def validate(outputs: NativeOutputs) -> NativeOutputs:
            if (
                entry.node.kind != "metric"
                or outputs.keys() != entry.node.outputs.keys()
            ):
                message = "Cached output names differ from the accepted metric"
                raise ValueError(message)
            value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
            return plugin.normalize_result(invocation.node, value)

        return self.cache.read(key, validate)

    def _dependencies(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
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
        completed: RunState,
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
            # Equivalent declarations may occur on different consumers. Nested
            # bindings still resolve against their original owner on a miss.
            owner_inputs = json.loads(owner.identity)
            owner_inputs.pop("node")
            key = (
                producer.node.name,
                json.dumps(edge.dependency.model_dump(mode="json"), sort_keys=True),
                json.dumps(owner_inputs, sort_keys=True),
            )
            if key in completed.prepared:
                return completed.prepared[key]
            prepared = prepare_dependency(
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
        else:
            completed.prepared[key] = prepared
            return prepared
