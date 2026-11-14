"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

import json
import logging
import traceback
from collections import UserDict
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from itertools import starmap
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from lyra.contracts.accepted import PreparedExecution, PreparedInput
from lyra.contracts.dependencies import Input, describe_dependency
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    MetricResultError,
)
from lyra.contracts.locations import LocationSelection
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
from lyra.runtime.locations import LocationResolver
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.operations import (
    OperationResult,
    acquire_operation_targets,
    execute_operation,
    forward_correspondences,
)
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.bindings import (
    BoundInvocation,
    BoundLocation,
    bind_execution,
    bind_resolved,
)
from lyra.runtime.planning.computation import (
    executable_contract,
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
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultDescriptions,
    ResultOutput,
)
from lyra.runtime.results import ExecutionMetadata, RunOutputMetadata
from lyra.runtime.reuse import publication_required, reuse_allowed
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from lyra.sdk.storage_schema import empty_frame

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.contracts.accepted import AcceptedSource
    from lyra.contracts.dependencies import LocationDependency
    from lyra.contracts.locations import LocationContractReference
    from lyra.contracts.metric_scope import OutputScope
    from lyra.contracts.outputs import OutputMetadata
    from lyra.contracts.plugins import NodeManifest
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.invocation import BoundPlugin
    from lyra.runtime.planning.catalog import CompiledCatalog
    from lyra.runtime.planning.graph import PluginGraph
    from lyra.runtime.planning.providers import SelectedLocationProvider
    from lyra.runtime.provider_records import ProviderRecords
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
    node: str
    computations: tuple[tuple[str | None, str], ...]


@dataclass(frozen=True)
class ExecutionOutcome:
    """Root tables, their durable description reference, and execution observations."""

    outputs: NativeOutputs
    result_ref: str | None
    provenance: ExecutionProvenance
    metadata: ExecutionMetadata
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
        self.root_artifact: str | None = None
        self.root_identity: str | None = None
        self.parameter_uses: dict[str, dict] = {}
        self.routes: dict[str, AcceptedSource] = {}
        self.memberships: dict[tuple[int, bool], BoundLocation] = {}
        self.bound_root: BoundInvocation | None = None
        self.completions: dict[tuple[str, str], MetricCompletion] = {}

    def observe_partial_reuse(
        self, node: str, identity: str, path: tuple[str, ...], *, mixed: bool
    ) -> None:
        """Record retained work alongside misses, even if later execution fails."""
        if mixed:
            self.observations.append(
                InvocationObservation(
                    node, sha256(identity.encode()).hexdigest(), path, "partial_hit"
                )
            )

    def clear(self) -> None:
        """Release every run-owned lookup."""
        super().clear()
        self.resolver = None
        self.prepared.clear()
        self.memberships.clear()
        self.completions.clear()
        self.parameter_uses.clear()
        self.routes.clear()
        self.bound_root = None


@dataclass
class GraphExecutor:
    """Execute graphs using registered implementations and job-scoped services.

    Plugins are keyed by the distribution names used to build the graph. The
    context factory supplies the current node's identity, preserving
    the caller's job association. It is called only for context-taking handlers.
    Each execute call owns a fresh lookup, including when this executor is reused.
    Required terminal values and cache-enabled metrics share relational storage.
    """

    catalog: CompiledCatalog
    plugins: Mapping[str, BoundPlugin]
    context_factory: Callable[[str], RunContext]
    resource_config: Mapping[str, ResourceConfig] | None = None
    resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None

    store: SQLiteStore | None = field(default=None, kw_only=True)
    provider_records: ProviderRecords | None = field(default=None, kw_only=True)
    retained_providers: frozenset[str] = field(default=frozenset(), kw_only=True)

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
            resolver = LocationResolver(
                scope,
                providers=self.catalog.provider_registry,
                provider_records=self.provider_records,
                retained_providers=self.retained_providers,
            )
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

    @staticmethod
    def persistent_key(identity: str) -> str:
        """Return the declared computation key directly for stored values."""
        return identity

    def execute(
        self,
        root: PreparedResolvedInvocation | PreparedExecution,
    ) -> ExecutionOutcome:
        """Run one prepared public metric and return all its native outputs.

        Returns:
            The requested metric's tables, description reference, and provenance.

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
            result, metadata = self._run_scoped(root, completed)
            if self.retain_result:
                self._retain_result(metadata, completed)
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

    def _run_scoped(
        self, root: PreparedResolvedInvocation | PreparedExecution, completed: RunState
    ) -> tuple[NativeOutputs, ExecutionMetadata]:
        scope = ResourceScope(
            self._collect_resources(), self.resource_config, self.resource_overrides
        )
        with scope.activate():
            bound = completed.bound_root
            primary = (
                root.locations.get(bound.primary_input or "")
                if isinstance(root, PreparedResolvedInvocation) and bound is not None
                else None
            )
            if (
                primary is not None
                and not primary.ids
                and bound is not None
                and bound.per_location
                and bound.primary_input is not None
            ):
                bound = replace(
                    bound,
                    locations=dict(bound.locations)
                    | {
                        bound.primary_input: bound.locations[
                            bound.primary_input
                        ].select(frozenset())
                    },
                )
                return self._run_bound_metric(bound, completed, scope)
            if isinstance(root, PreparedExecution) and bound is not None:
                return self._run_bound_metric(bound, completed, scope)
            return self._execute_scoped(root, scope, completed)

    def _resolve_bound(
        self,
        bound: BoundInvocation,
        completed: RunState,
        path: tuple[str, ...] = (),
        *,
        retain_membership: bool = False,
    ) -> BoundInvocation:
        """Return bindings with membership from complete correspondences.

        Raises:
            GraphExecutionError: If an upstream operation fails.
        """
        path = path or (bound.node,)
        # Off producers still supply membership needed to identify cached consumers.
        # Final result retention alone does not require these intermediate mappings.
        retain_membership = retain_membership or reuse_allowed(
            self.graph.nodes[bound.node].node.cache
        )
        locations = {}
        names = list(bound.locations)
        if bound.per_location and bound.primary_input is not None:
            names.remove(bound.primary_input)
            names.insert(0, bound.primary_input)
        for name in names:
            primary = locations.get(bound.primary_input or "") or bound.locations.get(
                bound.primary_input or ""
            )
            if bound.per_location and primary is not None and primary.ids == ():
                return replace(bound, locations=dict(bound.locations) | locations)
            location = bound.locations[name]
            if location.upstream is None:
                locations[name] = location
                continue
            membership_key = (id(location), retain_membership)
            if membership_key not in completed.memberships:
                upstream = self._resolve_bound(
                    location.upstream,
                    completed,
                    (*path, location.upstream.node),
                    retain_membership=retain_membership,
                )
                try:
                    groups = self._bound_correspondences(
                        upstream,
                        None,
                        completed,
                        membership=True,
                        retain_membership=retain_membership,
                    )
                except GraphExecutionError:
                    raise
                except Exception as exc:
                    raise GraphExecutionError(
                        upstream.node,
                        (*path, upstream.node),
                        exc,
                        kind="location_operation",
                        receiving_input=location.binding_path,
                    ) from exc
                source = next(iter(upstream.locations.values()))
                keys = upstream.demand().computation_keys or {}
                references = (
                    tuple((key, group) for group, key in keys.items())
                    if upstream.per_location
                    else tuple((keys[None], group) for group in groups)
                    or ((keys[None], None),)
                )
                completed.memberships[membership_key] = replace(
                    location,
                    upstream=upstream,
                    source=source,
                    ids=tuple(
                        sorted(
                            {
                                target
                                for targets in groups.values()
                                for target in targets
                            }
                        )
                    ),
                    pairs=tuple(
                        (source, target)
                        for source, targets in groups.items()
                        for target in targets
                    ),
                    computations=references,
                )
            locations[name] = completed.memberships[membership_key]
        primary = locations.get(bound.primary_input or "")
        if bound.per_location and primary is not None and primary.ids == ():
            return replace(bound, locations=locations)
        return replace(
            bound,
            locations=locations,
            dependencies={
                name: self._resolve_bound(
                    value,
                    completed,
                    (*path, value.node),
                    retain_membership=retain_membership,
                )
                for name, value in bound.dependencies.items()
            },
        )

    def _bound_correspondences(
        self,
        bound: BoundInvocation,
        selected: frozenset[str] | None,
        completed: RunState,
        *,
        membership: bool = False,
        retain_membership: bool = False,
    ) -> dict[str, tuple[str, ...]]:
        """Return complete mappings for identity or demanded execution."""
        demand = bound.demand(selected if bound.per_location else None)
        source = next(iter(bound.locations.values()))
        keys = demand.computation_keys or {}
        node = self.graph.nodes[bound.node].node
        store = self.provider_records.store if self.provider_records else self.store
        pairs = OperationPairs(store) if store is not None else None
        if store is not None and not store.path.exists():
            store.initialize()
        groups = {}
        missing = set()
        selections = []
        for group, key in keys.items():
            header = Header(
                value_key=key,
                node_id=bound.node,
                output_scope="location",
                owner_scope=source.provider.provider_scope,
                target_scope=bound.descriptor.target,
            )
            ids = tuple(dict.fromkeys(source.ids or ())) if group is None else (group,)
            selections.append((header, ids))
            saved = (
                pairs.lookup(header, ids)
                if pairs is not None
                and ((membership and retain_membership) or reuse_allowed(node.cache))
                else None
            )
            if saved is None:
                missing.add(group)
            else:
                groups.update(saved)
        memo = completed.get(demand.identity)
        if isinstance(memo, OperationResult):
            # A later consumer may require retention of work already done in memory.
            if pairs is not None and publication_required(
                node.cache, required=retain_membership
            ):
                pairs.publish(tuple(selections), memo.groups)
            return dict(memo.groups)
        completed.observe_partial_reuse(
            bound.node,
            demand.identity,
            (bound.node,),
            mixed=bool(missing) and len(missing) < len(keys),
        )
        if missing:
            invocation = self._materialize_bound_invocation(bound, missing, completed)
            result = self._operation(
                invocation,
                completed,
                (bound.node,),
                retained=groups,
                retain_membership=retain_membership,
            )
            groups.update(result.groups)
            completed[demand.identity] = result
        elif keys and (not membership or not any(groups.values())):
            completed.observations.append(
                InvocationObservation(
                    bound.node,
                    sha256(demand.identity.encode()).hexdigest(),
                    (bound.node,),
                    "cache_hit",
                )
            )
        return groups

    def _materialize_location(
        self, location: BoundLocation, completed: RunState, name: str
    ) -> NativeLocationInput:
        """Return selected records with correspondence provenance.

        Raises:
            RuntimeError: If the execution resolver is unavailable.
        """
        resolver = completed.resolver
        if resolver is None:
            message = "Location acquisition requires a resolver"
            raise RuntimeError(message)
        if location.upstream is None:
            return resolver.resolve(
                {
                    name: LocationSelection(
                        provider=location.provider.collection,
                        ids=list(location.ids or ()),
                    )
                }
            )[name]
        selected_sources = frozenset(source for source, _ in location.pairs)
        self._bound_correspondences(location.upstream, selected_sources, completed)
        targets = resolver.resolve(
            {
                name: LocationSelection(
                    provider=location.provider.collection, ids=list(location.ids or ())
                )
            }
        )[name]
        groups: dict[str, tuple[str, ...]] = {}
        for source, target in location.pairs:
            groups[source] = (*groups.get(source, ()), target)
        source = next(iter(location.upstream.locations.values()))
        result = OperationResult(groups, source.provider, targets)
        forwarded = forward_correspondences(
            result,
            self.catalog.provider_registry[
                location.provider.collection
            ].schema.metadata,
        )
        return replace(
            forwarded, computations=location.computations, lineage=location.lineage
        )

    def _run_bound_metric(
        self,
        bound: BoundInvocation,
        completed: RunState,
        scope: ResourceScope,
    ) -> tuple[NativeOutputs, ExecutionMetadata]:
        """Execute known root partitions with acquisition driven by cache misses.

        Returns:
            Assembled outputs and metadata.

        Raises:
            TypeError: If the bound use is not a metric.
        """
        resolver = LocationResolver(
            scope,
            providers=self.catalog.provider_registry,
            provider_records=self.provider_records,
            retained_providers=self.retained_providers,
        )
        completed.resolver = resolver
        bound = self._resolve_bound(bound, completed)
        outputs = self._bound_metric(bound, None, (bound.node,), completed)
        completed.root_identity = bound.demand().identity
        node = self.graph.nodes[bound.node].node
        if node.kind != "metric":
            message = "Bound metric scheduling requires a metric"
            raise TypeError(message)
        primary = bound.locations.get(bound.primary_input or "")
        owner = primary.provider.provider_scope if primary else None
        return outputs, ExecutionMetadata(
            metric_name=bound.node,
            parameters=bound.parameter_values(),
            outputs={
                name: RunOutputMetadata(
                    scope=node.scope,
                    table_schema=description.model_copy(deep=True),
                    owner_scope=deepcopy(owner),
                )
                for name, description in node.outputs.items()
            },
        )

    def _bound_metric(
        self,
        bound: BoundInvocation,
        groups: frozenset[str] | None,
        path: tuple[str, ...],
        completed: RunState,
    ) -> NativeOutputs:
        """Schedule known metric partitions before materializing their inputs.

        Producer computation scope is independent of the consumer's selection.

        Returns:
            Complete outputs for the applicable computation demand.

        Raises:
            TypeError: If the bound use is not a metric.
            ValueError: If membership is unknown.
        """
        demand = bound.demand(groups if bound.per_location else None)
        key = demand.identity
        node = self.graph.nodes[bound.node].node
        if node.kind != "metric":
            message = "Bound metric scheduling requires a metric"
            raise TypeError(message)
        plugin = self.plugins[self.graph.nodes[bound.node].distribution]
        primary = bound.locations.get(bound.primary_input or "")
        keys = demand.computation_keys
        if keys is None:
            message = "Bound metric scheduling requires known membership"
            raise ValueError(message)
        store = self.provider_records.store if self.provider_records else self.store
        tables = MetricTables(store) if store is not None else None
        fragments = []
        missing = set()
        if key in completed:
            completed.observations.append(
                InvocationObservation(
                    bound.node, sha256(key.encode()).hexdigest(), path, "shared_reuse"
                )
            )
            return cast("NativeOutputs", completed[key])
        for group, value_key in keys.items():
            saved = (
                self._lookup_metric(
                    bound.node,
                    tables,
                    (
                        Header(
                            value_key=value_key,
                            node_id=bound.node,
                            output_scope=node.scope,
                            owner_scope=primary.provider.provider_scope
                            if primary
                            else None,
                        ),
                    ),
                    (group,) if group is not None else primary.ids if primary else None,
                )
                if tables is not None and reuse_allowed(node.cache)
                else None
            )
            if saved is None:
                missing.add(group)
            else:
                fragments.append(saved)
        completed.observe_partial_reuse(
            bound.node, key, path, mixed=bool(missing and fragments)
        )
        if missing:
            invocation = self._materialize_bound_invocation(bound, missing, completed)
            if len(path) == 1:
                completed.native_inputs.extend(
                    NativeInputSummary(name, len(value.frame))
                    for name, value in invocation.locations.items()
                )
            fragments.append(
                cast(
                    "NativeOutputs",
                    self._execute(invocation, path, completed, self.context_factory),
                )
            )
        outputs = (
            self._assemble_metric(fragments)
            if fragments
            else {
                name: empty_frame(schema)
                for name, schema in plugin.output_schemas(bound.node).items()
            }
        )
        completed[key] = outputs
        if not missing and keys:
            completed.observations.append(
                InvocationObservation(
                    bound.node, sha256(key.encode()).hexdigest(), path, "cache_hit"
                )
            )
        for name in node.outputs:
            completed.completions[key, name] = MetricCompletion(
                key,
                name,
                node.scope,
                primary.provider if primary else None,
                frozenset(group for group in keys if group is not None)
                if bound.per_location
                else frozenset(primary.ids or ())
                if primary
                else frozenset(),
                bound.node,
                tuple(keys.items()),
            )
        return outputs

    def _materialize_bound_invocation(
        self,
        bound: BoundInvocation,
        missing: set[str | None],
        completed: RunState,
    ) -> PreparedResolvedInvocation:
        """Acquire only the inputs needed by missing computations.

        Returns:
            Native arguments retaining their original bound identity.

        Raises:
            RuntimeError: If no resolver is active.
            LocationAcquisitionError: If provider acquisition fails.
            LocationSelectionError: If the selection is invalid.
            LocationProviderContractError: If returned records violate the contract.
        """
        if completed.resolver is None:
            message = "Bound invocation scheduling requires a resolver"
            raise RuntimeError(message)
        try:
            primary = bound.locations.get(bound.primary_input or "")
            if primary is not None and bound.per_location:
                primary = primary.select(
                    frozenset(group for group in missing if group is not None)
                )
            locations = {
                name: self._materialize_location(
                    (value.related(primary) or value) if primary is not None else value,
                    completed,
                    name,
                )
                for name, value in bound.locations.items()
            }
        except (
            LocationAcquisitionError,
            LocationSelectionError,
            LocationProviderContractError,
        ) as exc:
            exc.node = bound.node
            raise
        arguments: dict[str, object] = dict(locations)
        if self.graph.nodes[bound.node].node.parameters_schema is not None:
            arguments["parameters"] = bound.parameter_values()
        plugin = self.plugins[self.graph.nodes[bound.node].distribution]
        invocation = plugin.prepare_resolved_invocation(bound.node, arguments)
        return replace(
            invocation,
            parameters=invocation.inspect_parameters(),
            bound_use=bound,
            parameter_use_key=bound.parameter_use_key,
            bound_identity=True,
        )

    def _lookup_metric(
        self,
        node: str,
        tables: MetricTables,
        headers: tuple[Header, ...],
        owners: tuple[str, ...] | None,
    ) -> NativeOutputs | None:
        """Return validated complete stored outputs, or None on a miss.

        Raises:
            DamagedDataError: If a completed value violates its declaration.
        """
        if not tables.store.path.exists():
            tables.store.initialize()
        retained = [tables.lookup(header) for header in headers]
        if not retained or any(value is None for value in retained):
            return None
        saved = self._assemble_metric(
            [value for value in retained if value is not None]
        )
        plugin = self.plugins[self.graph.nodes[node].distribution]
        try:
            outputs = plugin.validate_retained_outputs(node, saved)
        except MetricResultError as error:
            message = "Retained metric violates its declared contract"
            raise DamagedDataError(message) from error
        if owners is not None and any(
            not frame.location_id.isin(owners).all() for frame in outputs.values()
        ):
            message = "Retained owners differ from primary selection"
            raise DamagedDataError(message)
        return outputs

    @staticmethod
    def _assemble_metric(fragments: list[NativeOutputs]) -> NativeOutputs:
        """Combine validated partitions by their existing owner and row identities.

        Returns:
            Native tables with unchanged logical row identities.
        """
        return {
            name: pd.concat([value[name] for value in fragments], ignore_index=True)
            for name in fragments[0]
        }

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
            completed.bound_root = bind_resolved(
                self.catalog,
                root.node,
                {
                    name: root.bound_use.locations[name]
                    if root.bound_use is not None
                    else BoundLocation(value.contract, value.ids)
                    for name, value in root.original().locations.items()
                },
                parameters.uses,
                parameters.routes,
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
        *,
        primary_demand: frozenset[str] | None = None,
    ) -> tuple[NativeOutputs, ExecutionMetadata]:
        resolver = LocationResolver(
            scope,
            providers=self.catalog.provider_registry,
            provider_records=self.provider_records,
            retained_providers=self.retained_providers,
        )
        completed.resolver = resolver
        if isinstance(root, PreparedResolvedInvocation):
            for value in root.original().locations.values():
                resolver.retain(value)

        def context_factory(node: str) -> RunContext:
            return self.context_factory(node)

        if isinstance(root, PreparedExecution):
            locations = self._prepared_locations(root, completed, primary_demand)
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
                NativeInputSummary(name, len(value.frame))
                for name, value in root.locations.items()
                if name not in recorded
            )

        if primary_demand is not None and isinstance(root, PreparedResolvedInvocation):
            primary_name = self.graph.nodes[root.node].node.primary_input
            if primary_name is not None:
                root = root.select_primary(primary_name, primary_demand)
        root = replace(
            root, parameters=root.inspect_parameters(), bound_use=completed.bound_root
        )
        root = self._derived_inputs(root, (root.node,), completed, context_factory)
        node = self.graph.nodes[root.node].node
        if node.kind != "metric":
            message = "Execution results require a metric root"
            raise TypeError(message)
        primary = root.locations[node.primary_input] if node.primary_input else None
        metadata = ExecutionMetadata(
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
        return cast("NativeOutputs", result), metadata

    def _retain_result(
        self,
        metadata: ExecutionMetadata,
        completed: RunState,
    ) -> None:
        storage = (
            nullcontext(self.provider_records.store)
            if self.provider_records is not None
            else nullcontext(self.store)
        )
        entry = self.graph.nodes[metadata.metric_name]
        plugin = self.plugins[entry.distribution]
        with storage as store:
            if store is None:
                message = "Result retention requires a configured store"
                raise ValueError(message)
            registrations = tuple(
                Registration.from_description(
                    metadata.metric_name, name, layout, output_scope=entry.node.scope
                )
                for name, layout in plugin.output_storage(metadata.metric_name).items()
            )
            store.initialize(registrations)
            outputs = {}
            for registration in registrations:
                completion = completed.completions[
                    completed.root_identity or "", registration.output_name
                ]
                outputs[registration.output_name] = ResultOutput(
                    registration_key=registration.key,
                    owner_scope=metadata.outputs[registration.output_name].owner_scope,
                    computations=tuple(
                        ComputationSelection(
                            value_key=key,
                            groups=(group,)
                            if group is not None
                            else tuple(sorted(completion.partitions))
                            if completion.scope == "location"
                            else None,
                        )
                        for group, key in completion.computations
                    ),
                )
            completed.root_artifact = ResultDescriptions(store).publish(
                ResultDescription(
                    metric_name=metadata.metric_name,
                    code_revision=entry.node.code_revision,
                    parameters=metadata.parameters,
                    output_names=tuple(sorted(metadata.outputs)),
                    outputs=outputs,
                )
            )

    def _prepared_locations(
        self,
        root: PreparedExecution,
        completed: RunState,
        primary_demand: frozenset[str] | None,
    ) -> dict[str, NativeLocationInput]:
        """Acquire accepted inputs after scheduling direct-source operations.

        Returns:
            Native inputs with correspondence identities preserved.

        Raises:
            RuntimeError: If the execution resolver is unavailable.
        """
        resolver = completed.resolver
        if resolver is None:
            message = "Input acquisition requires an active resolver"
            raise RuntimeError(message)
        primary_name = self.graph.nodes[root.node].node.primary_input
        restored = {}
        if completed.bound_root is not None:
            for item in root.inputs:
                location = completed.bound_root.locations[item.name]
                if location.upstream is not None:
                    value = self._reuse_bound_operation(
                        location.upstream, (root.node,), completed
                    )
                    if value is not None:
                        restored[item.name] = value
        needed_ids = (
            {primary_name: tuple(sorted(primary_demand))}
            if primary_name is not None and primary_demand is not None
            else {}
        )
        needed_ids.update(dict.fromkeys(restored, ()))
        locations = resolver.resolve_prepared(root, needed_ids=needed_ids)
        for item in root.inputs:
            value = restored.get(item.name)
            if value is None:
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
                NativeInputSummary(item.name, len(value.frame))
            )
        return locations

    def _reuse_bound_operation(
        self,
        bound: BoundInvocation,
        path: tuple[str, ...],
        completed: RunState,
    ) -> NativeLocationInput | None:
        """Schedule direct-source mapping reuse before source acquisition.

        Returns:
            Forwarded requested groups, or None when ordinary execution is needed.

        Raises:
            GraphExecutionError: If retained mappings or target acquisition fail.
        """
        try:
            return self._lookup_bound_operation(bound, path, completed)
        except Exception as exc:
            raise GraphExecutionError(
                bound.node, (*path, bound.node), exc, kind="location_operation"
            ) from exc

    def _lookup_bound_operation(
        self,
        bound: BoundInvocation,
        path: tuple[str, ...],
        completed: RunState,
    ) -> NativeLocationInput | None:
        """Restore direct-source groups and execute only missing partitions.

        Returns:
            Forwarded groups, or None when ordinary execution is needed.
        """
        node = self.graph.nodes[bound.node].node
        source = next(iter(bound.locations.values()))
        store = self.provider_records.store if self.provider_records else self.store
        if (
            node.kind != "location_operation"
            or node.cache not in {"full", "per_location"}
            or source.upstream is not None
            or source.ids is None
        ):
            return None
        if store is None or completed.resolver is None:
            return None
        key = bound.demand().identity
        keys = bound.demand().computation_keys
        if keys is None:
            return None
        if not store.path.exists():
            store.initialize()
        groups: dict[str, tuple[str, ...]] = {}
        missing: set[str | None] = set()
        for group, value_key in keys.items():
            header = Header(
                value_key=value_key,
                node_id=bound.node,
                output_scope="location",
                owner_scope=source.provider.provider_scope,
                target_scope=bound.descriptor.target,
            )
            saved = OperationPairs(store).lookup(
                header, source.ids if group is None else (group,)
            )
            if saved is None:
                missing.add(group)
            else:
                groups.update(saved)
        completed.observe_partial_reuse(
            bound.node,
            key,
            (*path, bound.node),
            mixed=bool(missing) and len(missing) < len(keys),
        )
        if missing:
            if not bound.per_location:
                return None
            invocation = self._materialize_bound_invocation(bound, missing, completed)
            result = self._operation(
                invocation,
                completed,
                (*path, bound.node),
                retained=groups,
            )
            target = self.catalog.provider_registry[str(node.target_provider)]
            forwarded = forward_correspondences(result, target.schema.metadata)
            return replace(
                forwarded,
                computations=tuple(
                    (value_key, group) for group, value_key in keys.items()
                ),
            )
        target = self.catalog.provider_registry[str(node.target_provider)]
        ids = sorted({target_id for values in groups.values() for target_id in values})
        targets = completed.resolver.resolve(
            {"targets": LocationSelection(provider=str(node.target_provider), ids=ids)}
        )["targets"]
        result = OperationResult(groups, source.provider, targets)
        forwarded = forward_correspondences(result, target.schema.metadata)
        completed.observations.append(
            InvocationObservation(
                bound.node,
                sha256(key.encode()).hexdigest(),
                (*path, bound.node),
                "shared_reuse"
                if isinstance(completed.get(key), OperationResult)
                else "cache_hit",
            )
        )
        return replace(
            forwarded,
            computations=(
                tuple((value_key, group) for group, value_key in keys.items())
                if bound.per_location
                else tuple((key, group) for group in groups) or ((key, None),)
            ),
        )

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
            invocation = self._identify(invocation, path, completed, context_factory)
            outputs = self._invoke(invocation, path, completed, context_factory)
            if isinstance(outputs, OperationResult):
                target = self.catalog.provider_registry[
                    outputs.targets.contract.collection
                ]
                return self._forward_operation(
                    outputs, invocation, target.schema.metadata
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
                        invocation.node,
                        tuple(invocation.computation_keys.items()),
                    )
            return outputs

    def _derived_inputs(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> PreparedResolvedInvocation:
        if invocation.bound_identity:
            return invocation
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

    @staticmethod
    def _forward_operation(
        outputs: OperationResult,
        invocation: PreparedResolvedInvocation,
        description: OutputMetadata,
    ) -> NativeLocationInput:
        forwarded = forward_correspondences(
            replace(outputs, source=next(iter(invocation.locations.values()))),
            description,
        )
        keys = invocation.computation_keys
        references = (
            tuple((key, group) for group, key in keys.items())
            if invocation.bound_use is not None and invocation.bound_use.per_location
            else tuple((keys[None], group) for group in outputs.groups)
            or ((keys[None], None),)
        )
        return replace(forwarded, computations=references)

    def _identify(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> PreparedResolvedInvocation:
        """Return resolved computation references without invoking metric handlers."""
        if invocation.bound_identity:
            if len(path) == 1:
                completed.root_identity = invocation.identity
            return invocation
        dependencies = {}
        for name, edge in self.graph.dependencies[invocation.node].items():
            dependency_path = (*path, edge.output.producer.node.name)
            prepared = self._prepare(
                invocation.original(), edge, dependency_path, completed, context_factory
            )
            prepared = self._derived_inputs(
                prepared, dependency_path, completed, context_factory
            )
            dependencies[name] = self._identify(
                prepared, dependency_path, completed, context_factory
            )
        identified = replace(
            invocation,
            parameters=invocation.inspect_parameters(),
            dependencies=dependencies,
        )
        if len(path) == 1:
            completed.root_identity = identified.identity
        return identified

    def _invoke(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs | NativeLocationInput | OperationResult:
        key = invocation.identity
        plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
        if invocation.kind == "location_operation" and plugin.has_no_primary_work(
            invocation
        ):
            self._dependencies(invocation, path, completed, context_factory)
            outputs = plugin.invoke(invocation, {}, context_factory)
            completed[key] = outputs
            return outputs
        if key in completed:
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    sha256(key.encode()).hexdigest(),
                    path,
                    "shared_reuse",
                )
            )
            return completed[key]
        if invocation.kind == "location_operation":
            if completed.resolver is None:
                message = "Operation target acquisition requires the execution scope"
                raise RuntimeError(message)
            result = self._operation(invocation, completed, path)
            completed[key] = result
            return result
        return self._metric(invocation, path, completed, context_factory)

    def _metric(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
    ) -> NativeOutputs:
        entry = self.graph.nodes[invocation.node]
        plugin = self.plugins[entry.distribution]
        key = invocation.identity
        primary = (
            invocation.original().locations[entry.node.primary_input]
            if entry.node.primary_input
            else None
        )
        selections = tuple(
            (
                Header(
                    value_key=value_key,
                    node_id=invocation.node,
                    output_scope=entry.node.scope,
                    owner_scope=primary.contract.provider_scope
                    if primary is not None
                    else None,
                ),
                tuple(sorted(set(primary.ids)))
                if primary is not None and group is None
                else (group,)
                if group is not None
                else (),
            )
            for group, value_key in invocation.computation_keys.items()
        )
        storage = (
            nullcontext(self.provider_records.store)
            if self.provider_records is not None
            else nullcontext(self.store)
        )
        with storage as store:
            tables = MetricTables(store) if store is not None else None
            fragments = []
            missing = []
            if tables is not None and reuse_allowed(entry.node.cache):
                for header, partitions in selections:
                    outputs = self._lookup_metric(
                        invocation.node,
                        tables,
                        (header,),
                        partitions if primary is not None else None,
                    )
                    if outputs is None:
                        missing.append((header, partitions))
                    else:
                        fragments.append(outputs)
                if fragments and not missing:
                    outputs = self._assemble_metric(fragments)
                    completed[key] = outputs
                    completed.observations.append(
                        InvocationObservation(
                            invocation.node,
                            sha256(key.encode()).hexdigest(),
                            path,
                            "cache_hit",
                        )
                    )
                    return outputs
                completed.observe_partial_reuse(
                    invocation.node, key, path, mixed=bool(fragments and missing)
                )
                if fragments and entry.node.primary_input is not None:
                    invocation = invocation.select_primary(
                        entry.node.primary_input,
                        frozenset(group for _, groups in missing for group in groups),
                    )
                    selections = tuple(missing)
            dependencies = self._dependencies(
                invocation, path, completed, context_factory
            )
            if not plugin.has_no_primary_work(invocation):
                completed.observations.append(
                    InvocationObservation(
                        invocation.node,
                        sha256(key.encode()).hexdigest(),
                        path,
                        "started",
                    )
                )
            outputs = plugin.invoke(invocation, dependencies, context_factory)
            if tables is not None and (
                publication_required(
                    entry.node.cache, required=len(path) == 1 and self.retain_result
                )
            ):
                self._publish_metric(invocation.node, tables, selections, outputs)
            if fragments:
                outputs = self._assemble_metric([*fragments, outputs])
            completed[key] = outputs
            if not plugin.has_no_primary_work(invocation):
                completed.observations.append(
                    InvocationObservation(
                        invocation.node,
                        sha256(key.encode()).hexdigest(),
                        path,
                        "completed",
                    )
                )
            return outputs

    def _publish_metric(
        self,
        node: str,
        tables: MetricTables,
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
        outputs: NativeOutputs,
    ) -> None:
        """Publish validated complete partitions across all named outputs."""
        plugin = self.plugins[self.graph.nodes[node].distribution]
        for header, partitions in selections:
            selected = (
                outputs
                if header.output_scope == "unpartitioned"
                else {
                    name: frame.loc[frame.location_id.isin(partitions)].copy(deep=True)
                    for name, frame in outputs.items()
                }
            )
            tables.publish(header, selected, plugin.output_storage(node), partitions)

    def _operation(
        self,
        invocation: PreparedResolvedInvocation,
        completed: RunState,
        path: tuple[str, ...],
        *,
        retained: dict[str, tuple[str, ...]] | None = None,
        retain_membership: bool = False,
    ) -> OperationResult:
        plugin = self.plugins[self.graph.nodes[invocation.node].distribution]
        node = self.graph.nodes[invocation.node].node
        if (
            node.kind != "location_operation"
            or node.target_provider is None
            or completed.resolver is None
        ):
            message = "Operation requires a fixed target and active resolver"
            raise ValueError(message)
        source = next(iter(invocation.locations.values()))
        target = self.catalog.provider_registry[node.target_provider]
        keys = invocation.computation_keys
        selections = tuple(
            (
                Header(
                    value_key=key,
                    node_id=invocation.node,
                    output_scope="location",
                    owner_scope=source.contract.provider_scope,
                    target_scope=target.scope,
                ),
                source.ids if group is None else (group,),
            )
            for group, key in keys.items()
        )
        # The caller-owned provider store is shared when available. Automatic
        # local/worker store configuration is completed in the later phases.
        storage = (
            nullcontext(self.provider_records.store)
            if self.provider_records is not None
            else nullcontext(self.store)
        )
        with storage as store:
            pairs = OperationPairs(store) if store is not None else None
            groups = dict(retained or {})
            missing_selections = selections
            if pairs is not None and reuse_allowed(node.cache):
                if not pairs.store.path.exists():
                    pairs.store.initialize()
                saved = list(starmap(pairs.lookup, selections))
                for mapping in saved:
                    if mapping is not None:
                        groups.update(mapping)
                missing_selections = tuple(
                    selection
                    for selection, mapping in zip(selections, saved, strict=True)
                    if mapping is None
                )
                if selections and not missing_selections:
                    completed.observations.append(
                        InvocationObservation(
                            invocation.node,
                            sha256(invocation.identity.encode()).hexdigest(),
                            path,
                            "cache_hit",
                        )
                    )
                    return acquire_operation_targets(
                        groups, source, node.target_provider, completed.resolver
                    )
            completed.observe_partial_reuse(
                invocation.node,
                invocation.identity,
                path,
                mixed=bool(missing_selections)
                and len(missing_selections) < len(selections),
            )
            work = invocation
            if node.cache == "per_location":
                work = invocation.select_primary(
                    next(iter(invocation.locations)),
                    frozenset(group for _, ids in missing_selections for group in ids),
                )
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    sha256(invocation.identity.encode()).hexdigest(),
                    path,
                    "started",
                )
            )
            result = execute_operation(
                plugin, work, completed.resolver, retained=groups
            )
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    sha256(invocation.identity.encode()).hexdigest(),
                    path,
                    "completed",
                )
            )
            if pairs is not None and publication_required(
                node.cache, required=retain_membership
            ):
                pairs.publish(
                    missing_selections,
                    {
                        group: result.groups[group]
                        for _, ids in missing_selections
                        for group in ids
                    },
                )
            return result

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
            bound = invocation.bound_use
            if bound is not None and invocation.bound_identity:
                dependency = bound.dependencies[name]
                primary = bound.locations.get(bound.primary_input or "")
                producer_primary = dependency.locations.get(
                    dependency.primary_input or ""
                )
                selected = (
                    producer_primary.related(
                        primary.select(
                            frozenset(
                                invocation.locations[bound.primary_input or ""].ids
                            )
                        )
                    )
                    if producer_primary is not None and primary is not None
                    else None
                )
                groups = frozenset(selected.ids or ()) if selected is not None else None
                try:
                    outputs = self._bound_metric(
                        dependency, groups, dependency_path, completed
                    )
                except GraphExecutionError:
                    raise
                except Exception as exc:
                    raise GraphExecutionError(
                        dependency.node,
                        dependency_path,
                        exc,
                        receiving_input=f"inputs.{name}",
                    ) from exc
                table = outputs[edge.dependency.output]
                if groups is not None:
                    table = table.loc[table.location_id.isin(groups)]
                dependencies[name] = table
                continue
            prepared = invocation.dependencies[name]
            consumer_primary = self.graph.nodes[invocation.node].node.primary_input
            producer_primary = producer.node.primary_input
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
            owner_inputs = {"computation": owner.identity}
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
