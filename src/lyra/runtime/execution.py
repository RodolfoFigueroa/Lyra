"""Sequential, in-process execution of a validated plugin graph."""

from __future__ import annotations

import logging
import traceback
from collections import UserDict
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.errors import (
    ExecutionPersistenceError,
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
)
from lyra.contracts.locations import LocationSelection
from lyra.contracts.provenance import (
    ExecutionObservations,
    ExecutionProvenance,
    InvocationObservation,
    NativeInputSummary,
)
from lyra.contracts.references import new_job_id
from lyra.runtime.input_capture import (
    capture_inputs,
    capture_skipped_inputs,
    remember_inputs,
)
from lyra.runtime.intervention_validation import validate_interventions
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.locations import LocationResolver
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.operations import (
    OperationResult,
    execute_operation,
    forward_pairs,
)
from lyra.runtime.persistence import persistence_boundary
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
from lyra.runtime.planning.parameters import (
    ParameterUseError,
    prepare_parameter_uses,
)
from lyra.runtime.planning.provider_registry import capture_provider
from lyra.runtime.report_collection import ReportCollection
from lyra.runtime.resources import ResourceScope
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultOutput,
    _ResultPublication,
)
from lyra.runtime.results import ExecutionMetadata, RunOutputMetadata
from lyra.runtime.reuse import publication_required, reuse_allowed
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import DamagedDataError, Header, SQLiteStore
from lyra.sdk.locations import LocationProvider
from lyra.sdk.storage_schema import empty_frame

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence

    import sqlalchemy as sa

    from lyra.contracts.interventions import AcceptedIntervention
    from lyra.contracts.locations import LocationContractReference
    from lyra.contracts.metric_scope import OutputScope
    from lyra.contracts.plugins import MetricManifest
    from lyra.contracts.reports import ExecutionReport
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.arguments import PreparedResolvedInvocation
    from lyra.runtime.native_inputs import NativeLocationInput
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
        self.job_id: str | None = None
        self.provenance: ExecutionProvenance | None = None
        self.kind = kind
        self.node = node
        self.path = path
        self.receiving_input = receiving_input
        if isinstance(cause, ExecutionPersistenceError) and isinstance(
            cause.__cause__, Exception
        ):
            cause = cause.__cause__
        receiving = f", receiving input {receiving_input!r}" if receiving_input else ""
        super().__init__(
            f"Node {node!r} ({kind}), path {' -> '.join(path)}{receiving}: {cause}"
        )


@contextmanager
def invocation_boundary(
    node: str,
    path: tuple[str, ...],
    *,
    kind: str = "metric",
    receiving_input: str | None = None,
) -> Generator[None]:
    """Attach graph context to failures at the originating invocation.

    Yields:
        Control to the node's execution.

    Raises:
        GraphExecutionError: If the invocation fails.
    """
    try:
        yield
    except GraphExecutionError:
        raise
    except Exception as exc:
        raise GraphExecutionError(
            node,
            path,
            exc,
            kind=kind,
            receiving_input=receiving_input or getattr(exc, "input_name", None),
        ) from exc


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


@dataclass
class _MetricDecision:
    """Reusable output and pending publication selections for one demand."""

    retained: dict[str, NativeOutputs] = field(default_factory=dict)
    missing: dict[str | None, tuple[Header, tuple[str, ...]]] = field(
        default_factory=dict
    )

    def observe(
        self,
        completed: RunState,
        bound: BoundInvocation | None,
        key: str,
        path: tuple[str, ...],
    ) -> None:
        if not self.retained:
            return
        completed.report.observe_computation(bound, self.retained, executed=False)
        completed.observations.append(
            InvocationObservation(
                path[-1],
                sha256(key.encode()).hexdigest(),
                path,
                "partial_hit" if self.missing else "cache_hit",
            )
        )


@dataclass(frozen=True)
class ExecutionOutcome:
    """Root tables, publication metadata, and execution observations."""

    outputs: NativeOutputs
    committed_report: ExecutionReport | None
    provenance: ExecutionProvenance
    metadata: ExecutionMetadata
    report_state: ReportCollection
    completions: tuple[MetricCompletion, ...] = ()

    @property
    def job_id(self) -> str:
        """Identifier of this run, including when retention is disabled."""
        return self.report_state.job_id


class RunState(UserDict[str, "NativeOutputs | NativeLocationInput | OperationResult"]):
    """Own completed outputs and successful preparation for one execution."""

    def __init__(self, report: ReportCollection | None = None) -> None:
        """Start an empty run."""
        super().__init__()
        self.report = report if report is not None else ReportCollection(new_job_id())
        self.resolver: LocationResolver | None = None
        self.observations: list[InvocationObservation] = []
        self.native_inputs: list[NativeInputSummary] = []
        self.committed_report: ExecutionReport | None = None
        self.root_identity: str | None = None
        self.memberships: dict[tuple[int, bool], BoundLocation] = {}
        self.bound_root: BoundInvocation | None = None
        self.input_selections: dict[str, tuple[str, ...]] = {}
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
        self.memberships.clear()
        self.input_selections.clear()
        self.completions.clear()
        self.bound_root = None
        self.report.release_bindings()


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
    context_factory: Callable[[str], RunContext]
    resource_config: Mapping[str, ResourceConfig] | None = None
    resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None

    store: SQLiteStore | None = field(default=None, kw_only=True)
    provider_records: ProviderRecords | None = field(default=None, kw_only=True)
    retained_providers: frozenset[str] = field(default=frozenset(), kw_only=True)

    retain_result: bool = field(default=False, kw_only=True)
    on_publish: Callable[[sa.Connection, ExecutionObservations], None] | None = field(
        default=None, kw_only=True
    )

    @property
    def graph(self) -> PluginGraph:
        """The captured execution graph."""
        return self.catalog.graph

    @property
    def providers(self) -> Mapping[str, SelectedLocationProvider]:
        """The captured provider declaration."""
        return self.catalog.providers

    def validate_plan(self, prepared: PreparedExecution) -> BoundInvocation:
        """Reject incompatible acceptance before acquiring any resource.

        Returns:
            The complete bound root and its dependency uses.
        """
        bound, _ = self._bind_plan(prepared)
        return bound

    def _bind_plan(
        self, prepared: PreparedExecution
    ) -> tuple[BoundInvocation, ResourceScope]:
        """Validate the plan and its effective resources before acquisition.

        Returns:
            The bound root and the resource scope validated for this execution.

        """
        validate_execution_plan(
            self.graph,
            prepared.node,
            {item.name: item.binding.operations for item in prepared.inputs},
            self.catalog.dependency_routes,
        )
        bound = bind_execution(self.catalog, prepared)
        self._validate_root(prepared.node)
        self._validate_scopes(prepared)
        scope = ResourceScope(
            self.catalog.resources, self.resource_config, self.resource_overrides
        )
        self._validate_providers(prepared, scope)
        return bound, scope

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
            if sources[item.name].get(source) != item.binding:
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

    def _validate_providers(
        self, prepared: PreparedExecution, scope: ResourceScope
    ) -> None:
        """Verify the live schema and concrete resource class before acquisition.

        Raises:
            ValueError: If the registration or its implementation has changed.
        """
        for name in prepared.required_scopes():
            live = self.catalog.provider_registry.get(name)
            if live is None:
                msg = f"Required provider {name!r} is unavailable"
                raise ValueError(msg)
            expected = executable_contract(live.declaration)
            pinned = prepared.intervention_targets.get(name)
            if pinned is not None and executable_contract(pinned) != expected:
                msg = (
                    f"Intervention target {name!r} differs from its pinned declaration"
                )
                raise ValueError(msg)
            provider = scope.providers.get(live.resource)
            if (
                provider is None
                or not issubclass(provider, LocationProvider)
                or executable_contract(
                    capture_provider(live.resource, provider).declaration
                )
                != expected
            ):
                msg = f"Location provider implementation differs: {name!r}"
                raise ValueError(msg)

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
        _, scope = self._bind_plan(prepared)
        with self._acquisition_scope(
            scope, prepared.request.interventions, ReportCollection(new_job_id())
        ) as resolver:
            return resolver.resolve_prepared(prepared)

    def execute(
        self,
        root: PreparedResolvedInvocation | PreparedExecution,
        *,
        report: ReportCollection | None = None,
    ) -> ExecutionOutcome:
        """Run one prepared public metric and return all its native outputs.

        Returns:
            The requested metric's tables, metadata, and provenance.

        Preparation, execution, and result failures raise GraphExecutionError,
        chained to the underlying exception.
        """
        self._provenance = None
        root = (
            root.model_copy(deep=True) if isinstance(root, PreparedExecution) else root
        )
        completed = RunState(report)
        succeeded = False
        failure: GraphExecutionError | None = None
        try:
            scope = self._preflight(root, completed)
            result, metadata = self._run_scoped(root, completed, scope)
            completed.report.finalize()
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
            if failure is not None:
                failure.job_id = completed.report.job_id
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
            completed.committed_report,
            deepcopy(self._provenance),
            metadata,
            completed.report,
            completions,
        )

    @contextmanager
    def _acquisition_scope(
        self,
        scope: ResourceScope,
        interventions: Sequence[AcceptedIntervention],
        report: ReportCollection,
        *,
        retain_result: bool = False,
    ) -> Generator[LocationResolver]:
        """Share validation resources with all subsequent acquisition.

        Yields:
            The shared location resolver after every intervention is valid.

        Raises:
            ValueError: If intervention validation has no configured store.
        """
        store = self.provider_records.store if self.provider_records else self.store
        if interventions or (retain_result and store is not None):
            with persistence_boundary(report.job_id):
                if store is None:
                    msg = "Intervention validation requires a configured store"
                    raise ValueError(msg)
                store.initialize()
        with scope.activate():
            if interventions and store is not None:
                validate_interventions(
                    interventions, self.catalog.provider_registry, scope, store, report
                )
            yield LocationResolver(
                scope,
                providers=self.catalog.provider_registry,
                interventions=interventions,
                provider_records=self.provider_records,
                retained_providers=self.retained_providers,
                report=report,
            )

    def _run_scoped(
        self,
        root: PreparedResolvedInvocation | PreparedExecution,
        completed: RunState,
        scope: ResourceScope,
    ) -> tuple[NativeOutputs, ExecutionMetadata]:
        interventions = (
            root.request.interventions if isinstance(root, PreparedExecution) else ()
        )
        with self._acquisition_scope(
            scope, interventions, completed.report, retain_result=self.retain_result
        ) as resolver:
            completed.resolver = resolver
            bound = completed.bound_root
            if bound is None:
                msg = "Preflight must bind the root before execution"
                raise RuntimeError(msg)
            if isinstance(root, PreparedExecution):
                for name, selection in root.request.inputs.items():
                    resolver.check_removals(name, selection)
                return self._run_bound_metric(bound, completed)
            for value in (root.original_locations or root.locations).values():
                resolver.retain(value)
            groups = None
            if root.original_locations is not None and bound.primary_input is not None:
                groups = frozenset(root.locations[bound.primary_input].ids)
            return self._run_bound_metric(bound, completed, groups=groups)

    def _resolve_bound(
        self,
        bound: BoundInvocation,
        completed: RunState,
        path: tuple[str, ...] = (),
        *,
        retain_membership: bool = False,
    ) -> BoundInvocation:
        """Return bindings with membership from complete correspondences."""
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
            if location.supplied is not None or location.upstream is None:
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
                with invocation_boundary(
                    upstream.node,
                    (*path, upstream.node),
                    kind="location_operation",
                    receiving_input=location.binding_path,
                ):
                    groups = self._bound_correspondences(
                        upstream,
                        None,
                        completed,
                        path=(*path, upstream.node),
                        retain_membership=retain_membership,
                    )
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
        path: tuple[str, ...] = (),
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
        missing = {}
        selections = []
        if selected is not None and bound.per_location:
            complete = completed.get(bound.demand().identity)
            if isinstance(complete, OperationResult):
                completed.report.observe_memory_reuse(bound, keys.values())
                return {group: complete.groups[group] for group in selected}
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
                and (
                    (selected is None and retain_membership)
                    or reuse_allowed(node.cache)
                )
                else None
            )
            if saved is None:
                missing[group] = (header, ids)
            else:
                groups.update(saved)
        complete = completed.get(demand.identity)
        if isinstance(complete, OperationResult):
            completed.report.observe_memory_reuse(bound, keys.values())
            # A later consumer may require retention of work already done in memory.
            if pairs is not None and publication_required(
                node.cache, required=retain_membership or self.retain_result
            ):
                with persistence_boundary(completed.report.job_id):
                    pairs.publish(tuple(selections), complete.groups)
            return dict(complete.groups)
        completed.report.observe_computation(
            bound,
            (key for group, key in keys.items() if group not in missing),
            executed=False,
        )
        completed.observe_partial_reuse(
            bound.node,
            demand.identity,
            (bound.node,),
            mixed=bool(missing) and len(missing) < len(keys),
        )
        if missing:
            invocation = self._materialize_bound_invocation(
                bound, set(missing), completed
            )
            result = self._operation(
                invocation,
                completed,
                path or (bound.node,),
                selections=tuple(missing.values()),
                retain_membership=retain_membership,
            )
            # Cached groups supply membership here. Acquire their records only
            # if a consumer subsequently materializes those targets.
            groups.update(result.groups)
        elif keys and (selected is not None or not any(groups.values())):
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
            RuntimeError: If the resolver or operation membership is unavailable.
        """
        resolver = completed.resolver
        if resolver is None:
            message = "Location acquisition requires a resolver"
            raise RuntimeError(message)
        if location.ids is None:
            message = "Location membership must be resolved before acquisition"
            raise RuntimeError(message)
        if location.supplied is not None:
            return location.supplied.for_membership(location.membership)
        if location.upstream is None:
            return resolver.resolve(
                {
                    name: LocationSelection(
                        provider=location.provider.provider_id,
                        ids=list(location.ids),
                    )
                },
                use_ids={name: completed.report.location_use_id(location)},
                lineages={name: location.lineage},
            )[name]
        selected_sources = frozenset(source for source, _ in location.pairs)
        self._bound_correspondences(location.upstream, selected_sources, completed)
        targets = resolver.resolve(
            {
                name: LocationSelection(
                    provider=location.provider.provider_id, ids=list(location.ids)
                )
            },
            use_ids={name: completed.report.target_use_id(location.upstream)},
        )[name]
        source = next(iter(location.upstream.locations.values()))
        return forward_pairs(
            location.membership,
            targets,
            source.provider,
            self.catalog.provider_registry[
                location.provider.provider_id
            ].schema.metadata,
        )

    def _run_bound_metric(
        self,
        bound: BoundInvocation,
        completed: RunState,
        *,
        groups: frozenset[str] | None = None,
    ) -> tuple[NativeOutputs, ExecutionMetadata]:
        """Execute known root partitions with acquisition driven by cache misses.

        Returns:
            Assembled outputs and metadata.

        Raises:
            TypeError: If the bound use is not a metric.
        """
        node = self.graph.nodes[bound.node].node
        if node.kind != "metric":
            message = "Bound metric scheduling requires a metric"
            raise TypeError(message)
        primary = bound.locations.get(bound.primary_input or "")
        if (
            bound.primary_input is not None
            and primary is not None
            and bound.per_location
            and groups is not None
            and not groups
        ):
            bound = replace(
                bound,
                locations=dict(bound.locations)
                | {bound.primary_input: primary.select(groups)},
            )
        bound = self._resolve_bound(bound, completed)
        primary = bound.locations.get(bound.primary_input or "")
        if node.cache == "off" and groups is not None and primary is not None:
            # Explicit native root demand limits handler inputs. Its dependencies
            # still refer to the complete original bindings.
            selected = primary.select(groups)
            bound = replace(
                bound,
                locations={
                    name: value.related(selected) or value
                    for name, value in bound.locations.items()
                },
            )
            primary = selected
        completed.bound_root = bound
        with invocation_boundary(bound.node, (bound.node,)):
            outputs = self._bound_metric(bound, groups, (bound.node,), completed)
        completed.root_identity = bound.demand(
            groups if bound.per_location else None
        ).identity
        if node.cache == "full" and groups is not None:
            outputs = {
                name: frame.loc[frame.location_id.isin(groups)].reset_index(drop=True)
                for name, frame in outputs.items()
            }
            for name in outputs:
                address = completed.root_identity, name
                completed.completions[address] = replace(
                    completed.completions[address], partitions=groups
                )
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
        primary = bound.locations.get(bound.primary_input or "")
        keys = demand.computation_keys
        if keys is None:
            message = "Bound metric scheduling requires known membership"
            raise ValueError(message)
        if key in completed:
            completed.report.observe_memory_reuse(bound, keys.values())
            completed.observations.append(
                InvocationObservation(
                    bound.node, sha256(key.encode()).hexdigest(), path, "shared_reuse"
                )
            )
            return cast("NativeOutputs", completed[key])
        decision = self._lookup_metric(
            node,
            keys,
            primary.provider if primary else None,
            primary.ids if primary else (),
        )
        decision.observe(completed, bound, key, path)
        fragments = list(decision.retained.values())
        if decision.missing:
            invocation = self._materialize_bound_invocation(
                bound, set(decision.missing), completed
            )
            if len(path) == 1:
                completed.native_inputs.extend(
                    NativeInputSummary(name, len(value.frame))
                    for name, value in invocation.locations.items()
                )
            fragments.append(
                self._compute_metric(
                    invocation,
                    path,
                    completed,
                    self.context_factory,
                    tuple(decision.missing.values()),
                )
            )
        outputs = self._assemble_metric(bound.node, fragments)
        completed[key] = outputs
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
        plugin = self.catalog.implementations[self.graph.nodes[bound.node].distribution]
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
        node: MetricManifest,
        keys: Mapping[str | None, str],
        owner: LocationContractReference | None,
        primary_ids: tuple[str, ...] | None,
    ) -> _MetricDecision:
        """Look up each requested computation before acquiring inputs for misses.

        Returns:
            Detached retained frames and the selections to compute and publish.

        Raises:
            DamagedDataError: If stored outputs differ from the declared inventory.
        """
        store = self.provider_records.store if self.provider_records else self.store
        tables = (
            MetricTables(store)
            if store is not None and reuse_allowed(node.cache)
            else None
        )
        if keys and tables is not None and not tables.store.path.exists():
            tables.store.initialize()
        decision = _MetricDecision()
        for group, key in keys.items():
            header = Header(
                value_key=key,
                node_id=node.name,
                output_scope=node.scope,
                owner_scope=owner.provider_scope if owner else None,
            )
            partitions = (
                (group,) if group is not None else tuple(sorted(set(primary_ids or ())))
            )
            outputs = (
                tables.lookup(header, expected_partitions=partitions)
                if tables is not None
                else None
            )
            if outputs is None:
                decision.missing[group] = header, partitions
            elif outputs.keys() != node.outputs.keys():
                msg = "Retained metric output names differ from the declaration"
                raise DamagedDataError(msg)
            else:
                decision.retained[key] = outputs
        return decision

    def _assemble_metric(
        self, node: str, fragments: list[NativeOutputs]
    ) -> NativeOutputs:
        """Return a complete set of named outputs from validated fragments."""
        if not fragments:
            plugin = self.catalog.implementations[self.graph.nodes[node].distribution]
            return {
                name: empty_frame(schema)
                for name, schema in plugin.output_schemas(node).items()
            }
        if len(fragments) == 1:
            return fragments[0]
        return {
            name: pd.concat([value[name] for value in fragments], ignore_index=True)
            for name in fragments[0]
        }

    def _preflight(
        self, root: PreparedResolvedInvocation | PreparedExecution, completed: RunState
    ) -> ResourceScope:
        """Classify structural failures for worker diagnostics.

        Returns:
            The validated resource scope for the entire run.

        Raises:
            ParameterUseError: If effective parameters are invalid.
            GraphExecutionError: If the root is not executable.
            PlanValidationError: If the selected plan is invalid.
        """
        try:
            scope = self._validate_execution(root, completed)
            if completed.bound_root is not None:
                completed.report.capture(
                    completed.bound_root,
                    self.catalog,
                    root if isinstance(root, PreparedExecution) else None,
                )
        except (ParameterUseError, GraphExecutionError):
            raise
        except Exception as exc:
            raise PlanValidationError(str(exc)) from exc
        return scope

    def _validate_execution(
        self, root: PreparedResolvedInvocation | PreparedExecution, completed: RunState
    ) -> ResourceScope:
        if isinstance(root, PreparedExecution):
            completed.bound_root, scope = self._bind_plan(root)
            return scope

        self._validate_root(root.node)
        validate_execution_plan(
            self.graph, root.node, {}, self.catalog.dependency_routes
        )
        entry = self.graph.nodes[root.node]
        arguments: dict[str, object] = dict(root.locations)
        if entry.node.parameters_schema is not None:
            arguments["parameters"] = root.parameter_values()
        plugin = self.catalog.implementations[entry.distribution]
        plugin.prepare_resolved_invocation(root.node, arguments)
        parameters = prepare_parameter_uses(
            self.catalog,
            root.node,
            root.parameter_values(),
        )
        supplied = {}
        for name, value in (root.original_locations or root.locations).items():
            original = (
                root.bound_use.locations[name]
                if root.bound_use is not None
                else BoundLocation(value.contract, value.ids)
            )
            supplied[name] = replace(
                original,
                provider=value.contract,
                ids=value.ids,
                pairs=value.membership.pairs,
                lineage=value.lineage,
                computations=value.computations,
                supplied=value,
                # An empty native target without computation references carries
                # only the identity of its declared upstream operation.
                declared_identity=original.identity
                if original.upstream is not None
                and original.ids is None
                and not value.ids
                and not value.computations
                and original.provider == value.contract
                else None,
            )
        completed.bound_root = bind_resolved(
            self.catalog,
            root.node,
            supplied,
            parameters.uses,
            parameters.routes,
        )
        return ResourceScope(
            self.catalog.resources, self.resource_config, self.resource_overrides
        )

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

    def _retain_result(self, metadata: ExecutionMetadata, completed: RunState) -> None:
        with persistence_boundary(completed.report.job_id):
            self._publish_result(metadata, completed)

    def _publish_result(
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
        plugin = self.catalog.implementations[entry.distribution]
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
            description = ResultDescription(
                metric_name=metadata.metric_name,
                code_revision=entry.node.code_revision,
                parameters=metadata.parameters,
                output_names=tuple(sorted(metadata.outputs)),
                outputs=outputs,
                input_computations=next(iter(outputs.values())).computations,
                location_inputs=capture_skipped_inputs(
                    store,
                    self.catalog,
                    completed.bound_root,
                    completed.input_selections,
                )
                if not next(iter(outputs.values())).computations
                and completed.bound_root is not None
                else InputBindings(),
            )
            report = completed.report.complete()

            def publish_job(connection: sa.Connection) -> None:
                if self.on_publish is not None:
                    self.on_publish(
                        connection,
                        ExecutionObservations(
                            tuple(completed.native_inputs),
                            tuple(completed.observations),
                            succeeded=True,
                        ),
                    )

            projection = _ResultPublication(store).publish_metadata(
                description, report, registrations, publish_job
            )
            metadata.location_inputs = projection.location_inputs
            completed.committed_report = report

    def _compute_metric(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
        context_factory: Callable[[str], RunContext],
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
    ) -> NativeOutputs:
        """Return the validated handler outputs after publishing the selected misses."""
        entry = self.graph.nodes[invocation.node]
        plugin = self.catalog.implementations[entry.distribution]
        key = invocation.identity
        store = self.provider_records.store if self.provider_records else self.store
        tables = MetricTables(store) if store is not None else None
        dependencies = self._dependencies(invocation, path, completed)
        if not plugin.has_no_primary_work(invocation):
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    sha256(key.encode()).hexdigest(),
                    path,
                    "started",
                )
            )
        retain = tables is not None and publication_required(
            entry.node.cache, required=len(path) == 1 and self.retain_result
        )
        with persistence_boundary(completed.report.job_id):
            captured = capture_inputs(
                store, self.catalog, invocation, selections, enabled=retain
            )
        remember_inputs(invocation, captured, completed.input_selections)
        outputs = plugin.invoke(invocation, dependencies, context_factory)
        if retain and tables is not None:
            with persistence_boundary(completed.report.job_id):
                tables.publish(
                    selections,
                    outputs,
                    plugin.output_storage(invocation.node),
                    location_inputs=captured,
                )
        if not plugin.has_no_primary_work(invocation):
            completed.report.observe_computation(
                invocation.bound_use,
                (header.value_key for header, _ in selections),
                executed=True,
            )
            completed.observations.append(
                InvocationObservation(
                    invocation.node,
                    sha256(key.encode()).hexdigest(),
                    path,
                    "completed",
                )
            )
        return outputs

    def _operation(
        self,
        invocation: PreparedResolvedInvocation,
        completed: RunState,
        path: tuple[str, ...],
        *,
        selections: tuple[tuple[Header, tuple[str, ...]], ...],
        retain_membership: bool = False,
    ) -> OperationResult:
        """Return and memoize the newly computed mappings after successful publication.

        Raises:
            RuntimeError: If target acquisition has no active resolver.
        """
        key = invocation.identity
        memo = completed.get(key)
        if isinstance(memo, OperationResult):
            return memo
        entry = self.graph.nodes[invocation.node]
        if completed.resolver is None:
            msg = "Operation target acquisition requires the execution scope"
            raise RuntimeError(msg)
        observation_id = sha256(key.encode()).hexdigest()
        completed.observations.append(
            InvocationObservation(invocation.node, observation_id, path, "started")
        )
        result = execute_operation(
            self.catalog.implementations[entry.distribution],
            invocation,
            completed.resolver,
            target_use_id=completed.report.target_use_id(invocation.bound_use),
        )
        store = self.provider_records.store if self.provider_records else self.store
        if store is not None and publication_required(
            entry.node.cache, required=retain_membership or self.retain_result
        ):
            with persistence_boundary(completed.report.job_id):
                OperationPairs(store).publish(selections, dict(result.groups))
        if next(iter(invocation.locations.values())).ids:
            completed.report.observe_computation(
                invocation.bound_use,
                invocation.computation_keys.values(),
                executed=True,
            )
        completed.observations.append(
            InvocationObservation(invocation.node, observation_id, path, "completed")
        )
        completed[key] = result
        return result

    def _dependencies(
        self,
        invocation: PreparedResolvedInvocation,
        path: tuple[str, ...],
        completed: RunState,
    ) -> dict[str, object]:
        dependencies: dict[str, object] = {}
        for name, edge in self.graph.dependencies[invocation.node].items():
            producer = edge.output.producer
            dependency_path = (*path, producer.node.name)
            bound = invocation.bound_use
            if bound is None:
                msg = "Dependency scheduling requires a bound invocation"
                raise RuntimeError(msg)
            dependency = bound.dependencies[name]
            primary = bound.locations.get(bound.primary_input or "")
            producer_primary = dependency.locations.get(dependency.primary_input or "")
            selected = (
                producer_primary.related(
                    primary.select(
                        frozenset(invocation.locations[bound.primary_input or ""].ids)
                    )
                )
                if producer_primary is not None and primary is not None
                else None
            )
            groups = (
                frozenset(selected.ids)
                if selected is not None and selected.ids is not None
                else None
            )
            with invocation_boundary(
                dependency.node, dependency_path, receiving_input=f"inputs.{name}"
            ):
                outputs = self._bound_metric(
                    dependency, groups, dependency_path, completed
                )
            table = outputs[edge.dependency.output]
            if groups is not None:
                table = table.loc[table.location_id.isin(groups)]
            dependencies[name] = table
        return dependencies
