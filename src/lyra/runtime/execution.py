"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

import json
import logging
import traceback
from collections import UserDict
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast

from lyra.contracts.accepted import PreparedExecution, PreparedInput
from lyra.contracts.dependencies import Input, describe_dependency
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
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
from lyra.runtime.locations import LocationResolver
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.computation import (
    executable_contract,
    validate_cache_revision,
)
from lyra.runtime.planning.graph import (
    ResolvedDependency,
    ResolvedLocationDependency,
    build_graph,
)
from lyra.runtime.planning.parameters import (
    ParameterUseError,
    prepare_parameter_uses,
    route_key,
    use_key,
)
from lyra.runtime.resources import ResourceScope, collect_resources

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.dependencies import LocationDependency
    from lyra.contracts.plugins import NodeManifest
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.catalog import CompiledCatalog
    from lyra.runtime.planning.graph import PluginGraph
    from lyra.runtime.planning.providers import SelectedLocationProvider
    from lyra.sdk.context import RunContext
    from lyra.sdk.resources import BaseIO


logger = logging.getLogger(__name__)


class PlanValidationError(ValueError):
    """An execution plan cannot be honored by the live catalog."""


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
        self.parameter_uses: dict[str, dict] = {}

    def clear(self) -> None:
        """Release every run-owned lookup."""
        super().clear()
        self.prepared.clear()
        self.parameter_uses.clear()
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
    resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None
    cache: TableCache | None = field(default=None, kw_only=True)

    cache_revision: str | None = field(default=None, kw_only=True)

    @property
    def graph(self) -> PluginGraph:
        """The captured execution graph."""
        return self.catalog.graph

    @property
    def providers(self) -> Mapping[str, SelectedLocationProvider]:
        """The captured provider declaration."""
        return self.catalog.providers

    def _collect_resources(self) -> dict[str, type[BaseIO[Any]]]:
        return collect_resources(
            {name: plugin.resources for name, plugin in self.plugins.items()}
        )

    def validate_plan(self, prepared: PreparedExecution) -> None:
        """Reject incompatible acceptance before acquiring any resource.

        Raises:
            ValueError: If accepted contracts, identities or revisions differ.
        """
        self._validate_implementations(prepared.node, prepared.inputs)
        try:
            identity = self.catalog.computation.plan(prepared.node, prepared.inputs)
        except KeyError as exc:
            msg = "Accepted computation is unavailable"
            raise ValueError(msg) from exc
        if identity != prepared.computation_identity:
            msg = "Computation identity differs from accepted plan"
            raise ValueError(msg)
        self._validate_root(prepared.node)
        if prepared.cache_revision != self.cache_revision:
            message = "Executor cache revision differs from prepared execution"
            raise ValueError(message)
        self._validate_scopes(prepared)

    def _validate_scopes(self, prepared: PreparedExecution) -> None:
        """Check named selections and pinned scopes against live declarations.

        Raises:
            ValueError: If selections or scopes differ.
        """
        root = self.graph.nodes[prepared.node].node
        if root.kind != "metric" or {
            name: executable_contract(output)
            for name, output in prepared.outputs.items()
        } != {
            name: executable_contract(output) for name, output in root.outputs.items()
        }:
            message = "Output declarations differ from the live metric"
            raise ValueError(message)
        sources = self.catalog.accepted_sources[prepared.node]
        if {item.name for item in prepared.inputs} != sources.keys():
            message = "Input names differ from the live metric"
            raise ValueError(message)
        for item in prepared.inputs:
            source = item.binding.source
            if sources[item.name].get(source.collection) != item.binding:
                message = (
                    f"Input {item.name!r}: selected route differs from live definitions"
                )
                raise ValueError(message)
        PreparedExecution.model_validate(prepared.model_dump())
        expected = self.catalog.revisions.plan(
            prepared.node,
            tuple(
                (item.binding.source, item.binding.operations)
                for item in prepared.inputs
            ),
        )
        if expected != prepared.revisions:
            message = (
                "Cannot honor pinned revision: scopes differ from live definitions"
            )
            raise ValueError(message)
        for provider in self.providers.values():
            binding = self.plugins.get(provider.distribution)
            if binding is None or not binding.matches_location_provider(
                provider.declaration
            ):
                message = (
                    "Accepted location provider implementation "
                    "differs or is unavailable"
                )
                raise ValueError(message)

    _provenance: ExecutionProvenance | None = field(default=None, init=False)

    @property
    def provenance(self) -> ExecutionProvenance | None:
        """Detached metadata for the latest execution."""
        return deepcopy(self._provenance)

    def acquire_sources(self, prepared: PreparedExecution) -> dict[str, Locations]:
        """Acquire prepared sources with executor resource ownership and cleanup.

        Live declarations must match the accepted provider scopes before
        acquisition. This does not execute routes.

        Returns:
            Validated source batches keyed by receiving input.
        """
        prepared = prepared.model_copy(deep=True)
        self.validate_plan(prepared)
        resources = self._collect_resources()
        scope = ResourceScope(resources, self.resource_config, self.resource_overrides)
        with scope.activate():
            resolver = LocationResolver(scope, providers=self.catalog.provider_registry)
            return resolver.resolve_prepared(prepared)

    def _validate_implementations(
        self, node: str, inputs: tuple[PreparedInput, ...] = ()
    ) -> None:
        """Check the complete selected graph and its executable declarations.

        Raises:
            ValueError: If declarations, bindings, or implementations differ.
        """
        routes = {item.name: item.binding.operations for item in inputs}
        validate_execution_plan(self.graph, node, routes)
        required = self.graph.dependency_closure(
            [node, *(op for route in routes.values() for op in route)]
        )
        declarations = {}
        for name in required:
            entry = self.graph.nodes[name]
            binding = self.plugins.get(entry.distribution)
            manifest = binding.node_manifest(name) if binding is not None else None
            if manifest is None:
                msg = f"Accepted implementation is unavailable: {name}"
                raise ValueError(msg)
            if executable_contract(manifest) != executable_contract(entry.node):
                msg = f"Implementation differs from live declaration: {name}"
                raise ValueError(msg)
            declarations.setdefault(entry.distribution, []).append(entry.node)
        resolved = build_graph(declarations)
        for name in required:
            if (
                resolved.dependencies[name] != self.graph.dependencies[name]
                or resolved.location_dependencies[name]
                != self.graph.location_dependencies[name]
            ):
                msg = f"Dependency bindings differ from live declarations: {name}"
                raise ValueError(msg)

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
        self._provenance = None
        root = (
            root.model_copy(deep=True) if isinstance(root, PreparedExecution) else root
        )
        completed = RunState()
        succeeded = False
        failure: GraphExecutionError | None = None
        try:
            self._preflight(root, completed)
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
            if isinstance(exc, ParameterUseError):
                failure = GraphExecutionError(
                    exc.cause.metric,
                    exc.path,
                    exc.cause,
                    receiving_input=exc.binding,
                    kind=exc.kind,
                )
                raise failure from exc.cause
            failure = GraphExecutionError(
                root.node,
                (root.node,),
                exc,
                receiving_input=exc.input_name
                if isinstance(
                    exc,
                    (
                        LocationAcquisitionError,
                        LocationProviderContractError,
                        LocationSelectionError,
                    ),
                )
                else None,
            )
            raise failure from exc
        else:
            succeeded = True
        finally:
            self._provenance = ExecutionProvenance(
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
        return ExecutionOutcome(
            result, completed.root_artifact, deepcopy(self._provenance)
        )

    def _preflight(
        self, root: PreparedResolvedInvocation | PreparedExecution, completed: RunState
    ) -> None:
        """Classify structural failures for worker diagnostics.

        Raises:
            ParameterUseError: If effective parameters are invalid.
            GraphExecutionError: If the root is not executable.
            PlanValidationError: If the selected plan is invalid.
        """
        try:
            self._validate_execution(root, completed)
        except (ParameterUseError, GraphExecutionError):
            raise
        except Exception as exc:
            raise PlanValidationError(str(exc)) from exc

    def _validate_execution(
        self, root: PreparedResolvedInvocation | PreparedExecution, completed: RunState
    ) -> None:
        if isinstance(root, PreparedExecution):
            self.validate_plan(root)
        else:
            self._validate_root(root.node)
            entry = self.graph.nodes[root.node]
            arguments: dict[str, object] = dict(root.locations)
            if entry.node.parameters_schema is not None:
                arguments["parameters"] = root.parameter_values()
            self.plugins[entry.distribution].prepare_resolved_invocation(
                root.node, arguments
            )
        completed.parameter_uses = (
            deepcopy(root.parameter_uses)
            if isinstance(root, PreparedExecution)
            else prepare_parameter_uses(
                replace(self.catalog, implementations=self.plugins),
                root.node,
                root.parameter_values(),
            )
        )

        if not isinstance(root, PreparedExecution):
            self._validate_implementations(root.node)

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
        resolver = LocationResolver(scope, providers=self.catalog.provider_registry)

        def context_factory(node: str) -> RunContext:
            return self.context_factory(node)

        if isinstance(root, PreparedExecution):
            locations = resolver.resolve_prepared(root)
            for item in root.inputs:
                value = locations[item.name]
                for index, operation in enumerate(item.binding.operations):
                    entry = self.graph.nodes[operation]
                    plugin = self.plugins[entry.distribution]
                    arguments: dict[str, object] = {
                        next(iter(entry.node.location_inputs)): value
                    }
                    if "parameters" in cast(
                        "dict", entry.node.request_schema["properties"]
                    ):
                        arguments["parameters"] = completed.parameter_uses[
                            route_key(item.name, index)
                        ]
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
            root = self._derived_inputs(root, (root.node,), completed, context_factory)
            recorded = {item.name for item in completed.native_inputs}
            completed.native_inputs.extend(
                NativeInputSummary(name, len(value), location_fingerprint(value))
                for name, value in root.locations.items()
                if name not in recorded
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
            invocation = self._derived_inputs(
                invocation, path, completed, context_factory
            )
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

    def _derived_inputs(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> PreparedResolvedInvocation:
        node = self.graph.nodes[invocation.node].node
        values = dict(invocation.locations)
        visiting: set[str] = set()

        def resolve_sources(
            dependency: LocationDependency, producer: NodeManifest
        ) -> None:
            for receiving, declaration in producer.location_inputs.items():
                if (
                    declaration.dependency is not None
                    and declaration.dependency.operation is not None
                ):
                    continue
                binding = dependency.locations.get(receiving)
                if binding is None or isinstance(binding, Input):
                    resolve(binding.path if isinstance(binding, Input) else receiving)
                else:
                    nested = self.graph.nodes[str(binding.operation)].node
                    resolve_sources(binding, nested)

        def resolve(name: str) -> None:
            if name in values:
                return
            if name in visiting:
                message = f"Cyclic derived input {name!r}"
                raise ValueError(message)
            visiting.add(name)
            edge = self.graph.location_dependencies[node.name][f"inputs.{name}"]
            resolve_sources(edge.dependency, edge.producer.node)
            owner = PreparedResolvedInvocation(
                invocation.node,
                invocation.inspect_parameters(),
                values,
                kind=invocation.kind,
            )
            nested_path = (*path, edge.producer.node.name)
            prepared = self._prepare(
                owner, edge, nested_path, completed, context_factory
            )
            values[name] = cast(
                "Locations",
                self._execute(
                    prepared,
                    nested_path,
                    completed,
                    context_factory,
                    receiving_input=name,
                ),
            )
            visiting.remove(name)

        for name in node.location_inputs:
            resolve(name)
        if values.keys() == invocation.locations.keys():
            return invocation
        return PreparedResolvedInvocation(
            invocation.node,
            invocation.inspect_parameters(),
            values,
            kind=invocation.kind,
        )

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

        def location_result(name: str, _binding: LocationDependency) -> Locations:
            nested = self.graph.location_dependencies[owner.node][
                f"{binding_path}.locations.{name}"
            ]
            nested_path = (*path, nested.producer.node.name)
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
                json.dumps(describe_dependency(edge.dependency), sort_keys=True),
                json.dumps(owner_inputs, sort_keys=True),
            )
            if key in completed.prepared:
                return completed.prepared[key]
            prepared = prepare_dependency(
                owner,
                edge,
                self.plugins[producer.distribution],
                location_result=location_result,
                effective_parameters=completed.parameter_uses[
                    use_key(
                        owner.node,
                        owner.parameter_values(),
                        binding_path,
                    )
                ],
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
