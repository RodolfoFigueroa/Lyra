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
from lyra.runtime.arguments import (
    PreparedResolvedInvocation,
    prepare_dependency,
)
from lyra.runtime.artifacts import StoredOperation
from lyra.runtime.cache import CachedArtifact, TableCache
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput, input_fingerprint
from lyra.runtime.operations import (
    OperationResult,
    acquire_operation_targets,
    execute_operation,
    forward_correspondences,
)
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.bindings import BoundInvocation, bind_execution
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
    receiving_key,
    route_key,
    use_key,
)
from lyra.runtime.resources import ResourceScope, collect_resources
from lyra.runtime.results import RunMetadata, RunOutputMetadata

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.accepted import AcceptedSource
    from lyra.contracts.dependencies import LocationDependency
    from lyra.contracts.locations import LocationContractReference
    from lyra.contracts.metric_scope import OutputScope
    from lyra.contracts.plugins import MetricManifest, NodeManifest
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.bindings import BoundLocation
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
class MetricCompletion:
    """Successful named-output work, including partitions that produced no rows."""

    invocation: str
    output: str
    scope: OutputScope
    provider_scope: LocationContractReference | None
    partitions: frozenset[str]


@dataclass(frozen=True)
class ExecutionOutcome:
    """Root tables, their retained artifact, and observations from one execution."""

    outputs: NativeOutputs
    artifact_id: str | None
    provenance: ExecutionProvenance
    metadata: RunMetadata
    completions: tuple[MetricCompletion, ...] = ()


class RunState(UserDict[str, "NativeOutputs | NativeLocationInput | OperationResult"]):
    """Own completed outputs and successful preparation for one execution."""

    def __init__(self) -> None:
        """Start an empty run."""
        super().__init__()
        self.resolver: LocationResolver | None = None
        self.observations: list[InvocationObservation] = []
        self.native_inputs: list[NativeInputSummary] = []
        self.prepared: dict[tuple[str, str, str], PreparedResolvedInvocation] = {}
        self.metadata: tuple[str, Mapping[str, str]] | None = None
        self.artifacts: dict[str, str | None] = {}
        self.root_artifact: str | None = None
        self.parameter_uses: dict[str, dict] = {}
        self.routes: dict[str, AcceptedSource] = {}
        self.bound_root: BoundInvocation | None = None
        self.completions: dict[tuple[str, str], MetricCompletion] = {}

    def clear(self) -> None:
        """Release every run-owned lookup."""
        super().clear()
        self.resolver = None
        self.prepared.clear()
        self.completions.clear()
        self.parameter_uses.clear()
        self.routes.clear()
        self.bound_root = None
        self.artifacts.clear()
        self.metadata = None


@dataclass
class GraphExecutor:
    """Execute graphs using registered implementations and job-scoped services.

    Plugins are keyed by the distribution names used to build the graph. The
    context factory supplies the current node's identity, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    Workers require terminal publication with retain_result; local cache writes
    remain best effort.
    """

    catalog: CompiledCatalog
    plugins: Mapping[str, BoundPlugin]
    context_factory: Callable[[str], RunContext]
    resource_config: Mapping[str, ResourceConfig] | None = None
    resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None
    cache: TableCache | None = field(default=None, kw_only=True)

    cache_revision: str | None = field(default=None, kw_only=True)
    retain_result: bool = field(default=False, kw_only=True)

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

    def validate_plan(self, prepared: PreparedExecution) -> BoundInvocation:
        """Reject incompatible acceptance before acquiring any resource.

        Returns:
            The complete bound root and its dependency uses.

        Raises:
            ValueError: If accepted contracts, identities or revisions differ.
        """
        self._validate_implementations(prepared.node, prepared.inputs)
        bound = bind_execution(self.catalog, prepared)
        try:
            identity = self.catalog.computation.plan(
                prepared.node,
                prepared.inputs,
                prepared.dependency_routes,
                prepared.parameter_uses,
            )
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
        resources = self._collect_resources()
        for plugin in self.plugins.values():
            plugin.definition.validate_resources(resources)
        ResourceScope(resources, self.resource_config, self.resource_overrides)
        return bound

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
            tuple(
                (route.source, route.operations)
                for route in prepared.dependency_routes.values()
            ),
        )
        if expected != prepared.revisions:
            message = (
                "Cannot honor pinned revision: scopes differ from live definitions"
            )
            raise ValueError(message)
        for provider in self.providers.values():
            if provider.declaration.contract.reference not in {
                item.contract for item in prepared.revisions.bindings
            }:
                continue
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

    def acquire_sources(
        self, prepared: PreparedExecution
    ) -> dict[str, NativeLocationInput]:
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
        required = validate_execution_plan(
            self.graph, node, routes, self.catalog.dependency_routes
        )
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

        resolved = build_graph(
            {owner: plugin.definition for owner, plugin in self.plugins.items()}
        )
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
                result, metadata = self._execute_scoped(root, scope, completed)
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
            completions = tuple(completed.completions.values())
            completed.clear()
        return ExecutionOutcome(
            result,
            completed.root_artifact,
            deepcopy(self._provenance),
            metadata,
            completions,
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
            completed.bound_root = self.validate_plan(root)
        else:
            self._validate_root(root.node)
            entry = self.graph.nodes[root.node]
            arguments: dict[str, object] = dict(root.locations)
            if entry.node.parameters_schema is not None:
                arguments["parameters"] = root.parameter_values()
            self.plugins[entry.distribution].prepare_resolved_invocation(
                root.node, arguments
            )
        if isinstance(root, PreparedExecution):
            completed.parameter_uses = deepcopy(root.parameter_uses)
            completed.routes = {
                item.name: item.binding for item in root.inputs
            } | deepcopy(root.dependency_routes)
        else:
            parameters = prepare_parameter_uses(
                replace(self.catalog, implementations=self.plugins),
                root.node,
                root.parameter_values(),
            )
            completed.parameter_uses = parameters.uses
            completed.routes = parameters.routes

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
    ) -> tuple[NativeOutputs, RunMetadata]:
        resolver = LocationResolver(scope, providers=self.catalog.provider_registry)
        completed.resolver = resolver
        if isinstance(root, PreparedResolvedInvocation):
            for value in root.locations.values():
                resolver.retain(value)

        def context_factory(node: str) -> RunContext:
            return self.context_factory(node)

        if isinstance(root, PreparedExecution):
            locations = resolver.resolve_prepared(root)
            for item in root.inputs:
                value = self._execute_route(
                    locations[item.name],
                    item.name,
                    (root.node,),
                    completed,
                    bound=completed.bound_root.locations[item.name]
                    if completed.bound_root is not None
                    else None,
                )
                locations[item.name] = value
                completed.native_inputs.append(
                    NativeInputSummary(
                        item.name, len(value.frame), input_fingerprint(value)
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
            root = replace(
                root,
                parameters=root.inspect_parameters(),
                bound_use=completed.bound_root,
            )
            root = self._derived_inputs(root, (root.node,), completed, context_factory)
            recorded = {item.name for item in completed.native_inputs}
            completed.native_inputs.extend(
                NativeInputSummary(name, len(value.frame), input_fingerprint(value))
                for name, value in root.locations.items()
                if name not in recorded
            )

        root = self._derived_inputs(root, (root.node,), completed, context_factory)
        node = self.graph.nodes[root.node].node
        if node.kind != "metric":
            message = "Execution results require a metric root"
            raise TypeError(message)
        primary = root.locations[node.primary_input] if node.primary_input else None
        metadata = RunMetadata(
            metric_name=root.node,
            parameters=root.parameter_values(),
            outputs={
                name: RunOutputMetadata(
                    scope=node.scope,
                    table_schema=description.model_copy(deep=True),
                    owner_scope=deepcopy(primary.contract.provider_scope)
                    if primary is not None
                    else None,
                )
                for name, description in node.outputs.items()
            },
        )
        result = self._execute(root, (root.node,), completed, context_factory)
        self._retain_root(root, cast("NativeOutputs", result), metadata, completed)
        return cast("NativeOutputs", result), metadata

    def _execute_route(
        self,
        value: NativeLocationInput,
        key: str,
        path: tuple[str, ...],
        completed: RunState,
        *,
        bound: BoundLocation | None,
    ) -> NativeLocationInput:
        """Execute accepted hops with their captured parameters and bound uses.

        Returns:
            Correspondence records retaining the original binding relationship.
        """
        route = completed.routes[key]
        uses = []
        location = bound
        for _ in route.operations:
            upstream = location.upstream if location is not None else None
            uses.append(upstream)
            location = next(iter(upstream.locations.values())) if upstream else None
        for index, operation in enumerate(route.operations):
            entry = self.graph.nodes[operation]
            arguments: dict[str, object] = {
                next(iter(entry.node.location_inputs)): value
            }
            parameter_key = route_key(key, index)
            if entry.node.parameters_schema is not None:
                arguments["parameters"] = completed.parameter_uses[parameter_key]
            hop = self.plugins[entry.distribution].prepare_resolved_invocation(
                operation, arguments
            )
            hop = replace(
                hop,
                parameters=hop.inspect_parameters(),
                parameter_use_key=parameter_key,
                bound_use=uses[-index - 1],
            )
            value = cast(
                "NativeLocationInput",
                self._execute(
                    hop,
                    (*path, operation),
                    completed,
                    self.context_factory,
                    receiving_input=key,
                ),
            )
        return value

    def _retain_root(
        self,
        root: PreparedResolvedInvocation,
        result: NativeOutputs,
        metadata: RunMetadata,
        completed: RunState,
    ) -> None:
        """Retain terminal tables and resolved ownership independently of caching."""
        completed.root_artifact = completed.artifacts.get(root.identity)
        if (
            self.retain_result
            and self.cache is not None
            and completed.root_artifact is None
        ):
            node = self.graph.nodes[root.node]
            primary = (
                root.locations[node.node.primary_input]
                if node.node.primary_input
                else None
            )
            completed.root_artifact = self.cache.store.write_validated(
                result,
                self.plugins[node.distribution].output_storage(root.node),
                metadata.outputs,
                tuple(sorted(set(primary.ids))) if primary is not None else (),
            )

    def _execute(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
        receiving_input: str | None = None,
    ) -> NativeOutputs | NativeLocationInput | OperationResult:
        try:
            invocation = self._derived_inputs(
                invocation, path, completed, context_factory
            )
            outputs = self._invoke(invocation, path, completed, context_factory)
            if isinstance(outputs, OperationResult):
                target = self.catalog.provider_registry[
                    outputs.targets.contract.collection
                ]
                return forward_correspondences(
                    replace(outputs, source=next(iter(invocation.locations.values()))),
                    target.schema.metadata,
                )
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
        else:
            node = self.graph.nodes[invocation.node].node
            if node.kind == "metric":
                primary = (
                    invocation.locations[node.primary_input]
                    if node.primary_input is not None
                    else None
                )
                for name in node.outputs:
                    completed.completions[invocation.identity, name] = MetricCompletion(
                        invocation.identity,
                        name,
                        node.scope,
                        primary.contract if primary is not None else None,
                        frozenset(primary.ids) if primary is not None else frozenset(),
                    )
            return outputs

    def _derived_inputs(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> PreparedResolvedInvocation:
        if invocation.original_locations is not None:
            primary = self.graph.nodes[invocation.node].node.primary_input
            complete = self._derived_inputs(
                invocation.original(), path, completed, context_factory
            )
            if primary is not None:
                return complete.select_primary(
                    primary, frozenset(invocation.locations[primary].ids)
                )
            return complete
        return self._resolve_derived_inputs(
            invocation, path, completed, context_factory
        )

    def _resolve_derived_inputs(
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
                bound_use=invocation.bound_use,
                parameter_use_key=invocation.parameter_use_key,
            )
            nested_path = (*path, edge.producer.node.name)
            prepared = self._prepare(
                owner, edge, nested_path, completed, context_factory
            )
            values[name] = cast(
                "NativeLocationInput",
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
            bound_use=invocation.bound_use,
            parameter_use_key=invocation.parameter_use_key,
        )

    def _invoke(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs | NativeLocationInput | OperationResult:
        key = invocation.identity
        plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
        if plugin.has_no_primary_work(invocation):
            self._dependencies(invocation, path, completed, context_factory)
            outputs = plugin.invoke(invocation, {}, context_factory)
            completed[key] = outputs
            return outputs
        if key in completed:
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "shared_reuse"
                )
            )
            return completed[key]
        if invocation.kind == "location_operation":
            if completed.resolver is None:
                message = "Operation target acquisition requires the execution scope"
                raise RuntimeError(message)
            result = self._operation(invocation, plugin, completed, path)
            completed[key] = result
            return result
        node = self.graph.nodes[invocation.node].node
        cache_key = self._run_cache_key(key, completed) if node.cache != "off" else None
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
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "started"
                )
            )
            outputs = plugin.invoke(invocation, dependencies, context_factory)
            completed.observations.append(
                InvocationObservation(
                    invocation.node, TableCache.key(key), path, "completed"
                )
            )
            completed[key] = outputs
            if acquired and self.cache is not None and cache_key is not None:
                primary = (
                    invocation.locations[node.primary_input]
                    if node.primary_input
                    else None
                )
                descriptions = {
                    name: RunOutputMetadata(
                        scope=node.scope,
                        table_schema=description,
                        owner_scope=primary.contract.provider_scope
                        if primary
                        else None,
                    )
                    for name, description in cast(
                        "MetricManifest", node
                    ).outputs.items()
                }
                completed.artifacts[key] = self.cache.write_validated(
                    cache_key,
                    outputs,
                    plugin.output_storage(invocation.node),
                    descriptions,
                    tuple(sorted(set(primary.ids))) if primary else (),
                )
            return outputs

    def _operation(
        self,
        invocation: PreparedResolvedInvocation,
        plugin: BoundPlugin,
        completed: RunState,
        path: tuple[str, ...],
    ) -> OperationResult:
        node = self.graph.nodes[invocation.node].node
        if (
            node.kind != "location_operation"
            or node.target_provider is None
            or completed.resolver is None
        ):
            message = "Operation requires a fixed target and active resolver"
            raise ValueError(message)
        source = next(iter(invocation.locations.values()))
        key = (
            self._run_cache_key(invocation.identity, completed)
            if node.cache != "off"
            else None
        )
        target = self.catalog.provider_registry[node.target_provider]
        with (
            self.cache.lock(key)
            if self.cache is not None and key is not None
            else nullcontext(enter_result=False) as acquired
        ):
            if self.cache is not None and key is not None:
                cached = self.cache.read_operation(key)
                if cached is not None:
                    saved = cached.value
                    if (
                        saved.source_scope == source.contract
                        and saved.target_scope == target.contract.reference
                        and set(saved.groups) == set(source.ids)
                    ):
                        completed.observations.append(
                            InvocationObservation(
                                invocation.node,
                                TableCache.key(invocation.identity),
                                path,
                                "cache_hit",
                            )
                        )
                        return acquire_operation_targets(
                            saved.groups,
                            source,
                            node.target_provider,
                            completed.resolver,
                        )
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    TableCache.key(invocation.identity),
                    path,
                    "started",
                )
            )
            result = execute_operation(plugin, invocation, completed.resolver)
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    TableCache.key(invocation.identity),
                    path,
                    "completed",
                )
            )
            if acquired and self.cache is not None and key is not None:
                self.cache.write_validated_operation(
                    key,
                    StoredOperation.model_construct(
                        groups=dict(result.groups),
                        source_scope=result.source_scope,
                        target_scope=result.targets.contract,
                    ),
                )
            return result

    def _cached(
        self, invocation: PreparedResolvedInvocation, key: str
    ) -> CachedArtifact[NativeOutputs] | None:
        if self.cache is None:
            return None
        entry = self.graph.nodes[invocation.node]
        plugin = self.plugins[entry.distribution]

        def validate(outputs: NativeOutputs) -> NativeOutputs:
            if (
                entry.node.kind != "metric"
                or outputs.keys() != entry.node.outputs.keys()
            ):
                message = "Cached output names differ from the accepted metric"
                raise ValueError(message)
            value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
            return plugin.normalize_invocation_result(invocation, value)

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
                invocation.original(), edge, dependency_path, completed, context_factory
            )
            consumer_primary = self.graph.nodes[invocation.node].node.primary_input
            producer_primary = producer.node.primary_input
            prepared = self._derived_inputs(
                prepared, dependency_path, completed, context_factory
            )
            selected = (
                prepared.locations[producer_primary].related_selection(
                    invocation.locations[consumer_primary]
                )
                if consumer_primary is not None and producer_primary is not None
                else None
            )
            if (
                selected is not None
                and producer.node.cache == "per_location"
                and producer_primary is not None
            ):
                prepared = prepared.select_primary(
                    producer_primary, frozenset(selected.ids)
                )
            outputs = self._execute(
                prepared,
                dependency_path,
                completed,
                context_factory,
                receiving_input=f"inputs.{name}",
            )
            table = cast("NativeOutputs", outputs)[edge.dependency.output]
            if selected is not None:
                table = table.loc[table["location_id"].isin(selected.ids)]
            dependencies[name] = table
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
        parameter_key = use_key(
            owner.node,
            owner.parameter_values(),
            binding_path,
            scope=owner.bound_use.parameter_use_key
            if owner.bound_use is not None
            else owner.parameter_use_key,
        )
        bound = owner.bound_use.bindings[binding_path] if owner.bound_use else None

        def location_input(
            name: str, value: NativeLocationInput | LocationDependency
        ) -> NativeLocationInput:
            if not isinstance(value, NativeLocationInput):
                nested = self.graph.location_dependencies[owner.node][
                    f"{binding_path}.locations.{name}"
                ]
                nested_path = (*path, nested.producer.node.name)
                prepared = self._prepare(
                    owner, nested, nested_path, completed, context_factory
                )
                value = cast(
                    "NativeLocationInput",
                    self._execute(
                        prepared,
                        nested_path,
                        completed,
                        context_factory,
                        receiving_input=nested.binding_path,
                    ),
                )
            key = receiving_key(owner.parameter_use_key, binding_path, name)
            return self._execute_route(
                value,
                key,
                path,
                completed,
                bound=bound.locations[name] if bound is not None else None,
            )

        try:
            # Equivalent declarations may occur on different consumers. Nested
            # bindings still resolve against their original owner on a miss.
            owner_inputs = {
                key: value
                for key, value in json.loads(owner.identity).items()
                if key != "node"
            }
            owner_inputs["binding_roles"] = {
                name: id(value.lineage) for name, value in owner.locations.items()
            }
            key = (
                producer.node.name,
                json.dumps(
                    {
                        "binding": describe_dependency(edge.dependency),
                        "computation": bound.identity if bound is not None else None,
                        "parameters": completed.parameter_uses[parameter_key],
                    },
                    sort_keys=True,
                ),
                json.dumps(owner_inputs, sort_keys=True),
            )
            if key in completed.prepared:
                return completed.prepared[key]
            prepared = prepare_dependency(
                owner,
                edge,
                self.plugins[producer.distribution],
                location_input=location_input,
                effective_parameters=completed.parameter_uses[parameter_key],
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
            prepared = replace(
                prepared,
                parameters=prepared.inspect_parameters(),
                parameter_use_key=parameter_key,
                bound_use=bound,
            )
            completed.prepared[key] = prepared
            return prepared
