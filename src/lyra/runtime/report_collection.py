"""Private attempt-owned topology, collected without executing the selected graph."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from lyra.contracts.reports import (
    DataBinding,
    ExecutionReport,
    InterventionValidation,
    NodeOutcome,
    OperationReport,
    ParameterBinding,
    ReportNode,
    ReportOutput,
)
from lyra.contracts.value_identity import value_key
from lyra.runtime.planning.parameters import parameter_sources

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lyra.contracts.accepted import PreparedExecution, SubmittedRequest
    from lyra.contracts.origins import Origin
    from lyra.contracts.provider_scope import DatasetScope
    from lyra.runtime.planning.bindings import BoundInvocation, BoundLocation
    from lyra.runtime.planning.catalog import CompiledCatalog


@dataclass(frozen=True)
class ResolvedUse:
    """A report-local identity and named producing-use relationships."""

    use_id: str
    node: str
    kind: Literal["metric", "operation", "provider"]
    inputs: tuple[tuple[str, str], ...]
    target_use_id: str | None = None


@dataclass(frozen=True)
class OperationDescription:
    """Declared operation target, independent of stage observations."""

    target_scope: DatasetScope
    target_adapter: Literal["parquet", "postgis"] | None
    has_selector: bool


@dataclass(frozen=True)
class NodeDescription:
    """Detached static fields awaiting observed outcomes before publication."""

    parameters: dict
    primary_input: str | None
    inputs: dict[str, DataBinding]
    outputs: dict[str, ReportOutput]
    code_revision: str | None
    origins: list[Origin]
    operation: OperationDescription | None = None


@dataclass
class ComputationObservation:
    """Compact, monotonic facts for one resolved computation use."""

    executed: bool = False
    reused: bool = False

    @property
    def outcome(self) -> NodeOutcome:
        """Summarize observed work, including unvisited uses."""
        if self.executed:
            return "mixed" if self.reused else "executed"
        return "reused" if self.reused else "skipped"


@dataclass
class ReportCollection:
    """Own one attempt's unfinished collection, never a successful report.

    Binding tokens survive dataclass replacement and reduced scheduling demands.
    The index contains only the selected graph and does not own native tables.
    """

    execution_id: str = field(default_factory=lambda: uuid4().hex, init=False)
    root_use_id: str | None = field(default=None, init=False)
    uses: dict[str, ResolvedUse] = field(default_factory=dict, init=False)
    request: SubmittedRequest | None = field(default=None, init=False)
    build_revision: str | None = field(default=None, init=False)
    descriptions: dict[str, NodeDescription] = field(default_factory=dict, init=False)
    parameter_bindings: list[ParameterBinding] = field(default_factory=list, init=False)
    _keys: dict[str, str] = field(default_factory=dict, init=False)
    _bindings: dict[object, str] = field(default_factory=dict, init=False)
    _locations: dict[object, str] = field(default_factory=dict, init=False)

    computations: dict[str, ComputationObservation] = field(
        default_factory=dict, init=False
    )
    _computed: dict[str, set[str]] = field(default_factory=dict, init=False)

    providers: dict[str, ComputationObservation] = field(
        default_factory=dict, init=False
    )
    nodes: tuple[ReportNode, ...] | None = field(default=None, init=False)
    selectors: set[str] = field(default_factory=set, init=False)
    intervention_validations: list[InterventionValidation] = field(
        default_factory=list, init=False
    )
    target_queries: set[str] = field(default_factory=set, init=False)
    _acquired: dict[str, set[str]] = field(default_factory=dict, init=False)

    def finalize(self) -> None:
        """Assemble complete private node data after successful managed computation.

        Publication will bind the result reference to these descriptions and the
        captured request. No successful ExecutionReport exists at this boundary.
        """
        if self.request is None:
            return
        nodes = []
        for use_id, use in self.uses.items():
            description = self.descriptions[use_id]
            observation = (
                self.providers[use_id]
                if use.kind == "provider"
                else self.computations[use_id]
            )
            target = description.operation
            operation = None
            if target is not None:
                operation = OperationReport(
                    target_scope=target.target_scope,
                    target_adapter=target.target_adapter,
                    selector=("executed" if use_id in self.selectors else "skipped")
                    if target.has_selector
                    else "not_applicable",
                    target_query=(
                        "executed"
                        if use.target_use_id in self.target_queries
                        else "skipped"
                    )
                    if target.has_selector
                    else "not_applicable",
                    mapping="executed" if observation.executed else "skipped",
                )
            nodes.append(
                ReportNode(
                    use_id=use_id,
                    node_id=use.node,
                    kind=use.kind,
                    outcome=observation.outcome,
                    parameters=description.parameters,
                    primary_input=description.primary_input,
                    inputs=description.inputs,
                    outputs=description.outputs,
                    code_revision=description.code_revision,
                    origins=description.origins,
                    operation=operation,
                )
            )
        self.nodes = tuple(nodes)

    def complete(self, result_ref: str) -> ExecutionReport:
        """Bind finalized observations to the attempted final result.

        Returns:
            A detached complete successful report for atomic publication.

        Raises:
            ValueError: If managed collection has not completed.
        """
        if self.request is None or self.root_use_id is None or self.nodes is None:
            msg = "Managed report collection is incomplete"
            raise ValueError(msg)
        return ExecutionReport(
            request=self.request,
            execution_id=self.execution_id,
            result_ref=result_ref,
            status="succeeded",
            root_use_id=self.root_use_id,
            nodes=list(self.nodes),
            parameter_bindings=self.parameter_bindings,
            build_revision=self.build_revision,
            intervention_validations=self.intervention_validations,
        )

    def observe_selector(self, bound: BoundInvocation | None) -> None:
        """Record actual selector construction for a resolved operation use."""
        if bound is not None:
            self.selectors.add(self.use_id(bound))

    def target_use_id(self, bound: BoundInvocation | None) -> str | None:
        """Return the provider use declared by this operation, if bound."""
        return (
            self.uses[self.use_id(bound)].target_use_id if bound is not None else None
        )

    def observe_provider(
        self,
        use_id: str | None,
        ids: Iterable[str],
        *,
        executed: bool,
        query: bool = False,
    ) -> None:
        """Summarize acquisition without mistaking same-use consumption for reuse."""
        if use_id is None:
            return
        observation = self.providers[use_id]
        acquired = self._acquired.setdefault(use_id, set())
        selected = set(ids)
        if executed:
            observation.executed |= bool(selected) or query
            acquired.update(selected)
            if query:
                self.target_queries.add(use_id)
        elif selected - acquired:
            observation.reused = True

    def observe_computation(
        self, bound: BoundInvocation | None, keys: Iterable[str], *, executed: bool
    ) -> None:
        """Accumulate reuse while excluding repeated consumption by the same use."""
        if bound is None:
            return
        use_id = self.use_id(bound)
        observation = self.computations[use_id]
        computed = self._computed.setdefault(use_id, set())
        if executed:
            observation.executed = True
            computed.update(keys)
        elif any(key not in computed for key in keys):
            observation.reused = True

    def observe_memory_reuse(self, bound: BoundInvocation, keys: Iterable[str]) -> None:
        """Observe consumed completions, excluding synthetic zero-location work."""
        primary = bound.locations.get(bound.primary_input or "")
        if primary is None or primary.ids:
            self.observe_computation(bound, keys, executed=False)

    def index(self, root: BoundInvocation) -> None:
        """Capture all selected uses before acquisition and cache lookup."""
        self.root_use_id = self._visit(root)

    def capture(
        self,
        root: BoundInvocation,
        catalog: CompiledCatalog,
        prepared: PreparedExecution | None,
    ) -> None:
        """Capture accepted selections and declarations without acquiring records."""
        self.index(root)
        if prepared is None:
            return
        self.request = prepared.request.model_copy(deep=True)
        self.build_revision = prepared.build_revision
        self._capture_parameter_bindings(root, catalog)
        self._describe(root, catalog)
        for use_id, use in self.uses.items():
            if use.kind == "provider":
                scope = catalog.provider_registry[use.node].scope
                self.descriptions[use_id] = NodeDescription(
                    {}, None, {}, {}, scope.code_revision, deepcopy(list(scope.origins))
                )

    def _capture_parameter_bindings(
        self, root: BoundInvocation, catalog: CompiledCatalog
    ) -> None:
        visited: set[object] = set()
        connections: dict[tuple[str, str, str, str], ParameterBinding] = {}

        def visit(bound: BoundInvocation) -> None:
            if bound.report_token in visited:
                return
            visited.add(bound.report_token)
            edges = {
                f"inputs.{name}": edge
                for name, edge in catalog.graph.dependencies[bound.node].items()
            } | dict(catalog.graph.location_dependencies[bound.node])
            for path, target in bound.bindings.items():
                for receiving, source in parameter_sources(
                    bound.parameter_values(), edges[path]
                ).items():
                    key = (self.use_id(bound), source, self.use_id(target), receiving)
                    connections[key] = ParameterBinding(
                        source_use_id=key[0],
                        source_parameter=source,
                        target_use_id=key[2],
                        target_parameter=receiving,
                    )
                visit(target)
            for target in bound.dependencies.values():
                visit(target)
            for location in bound.locations.values():
                if location.upstream is not None:
                    visit(location.upstream)

        visit(root)
        self.parameter_bindings = list(connections.values())

    def _describe(self, bound: BoundInvocation, catalog: CompiledCatalog) -> None:
        use_id = self.use_id(bound)
        if use_id in self.descriptions:
            return
        node = catalog.graph.nodes[bound.node].node
        inputs = {
            name: DataBinding(
                source_use_id=self.location_use_id(value),
                output_name=None,
                provider_scope=deepcopy(value.provider.provider_scope),
            )
            for name, value in bound.locations.items()
        }
        inputs.update(
            {
                name: DataBinding(
                    source_use_id=self.use_id(value),
                    output_name=bound.outputs[name],
                    provider_scope=None,
                )
                for name, value in bound.dependencies.items()
            }
        )
        outputs = {}
        operation = None
        if node.kind == "metric":
            owner = (
                bound.locations[bound.primary_input].provider.provider_scope
                if bound.primary_input is not None
                else None
            )
            outputs = {
                name: ReportOutput(
                    scope=node.scope,
                    table_schema=deepcopy(schema),
                    owner_scope=deepcopy(owner),
                )
                for name, schema in node.outputs.items()
            }
            origins = list(dict.fromkeys(node.origins))
        else:
            target = bound.descriptor.target
            if target is None:
                msg = f"Missing bound target for operation {bound.node!r}"
                raise ValueError(msg)
            operation = OperationDescription(
                deepcopy(target),
                catalog.provider_registry[target.provider_id].adapter,
                node.has_target_selector,
            )
            origins = []
        self.descriptions[use_id] = NodeDescription(
            bound.parameter_values(),
            bound.primary_input,
            inputs,
            outputs,
            node.code_revision,
            deepcopy(origins),
            operation,
        )
        for location in bound.locations.values():
            if location.upstream is not None:
                self._describe(location.upstream, catalog)
        for dependency in (*bound.dependencies.values(), *bound.bindings.values()):
            self._describe(dependency, catalog)

    def use_id(self, bound: BoundInvocation) -> str:
        """Return the use ID for a complete, replaced, or reduced binding."""
        return self._bindings[bound.report_token]

    def location_use_id(self, location: BoundLocation) -> str:
        """Return the producer ID through location membership changes."""
        return self._locations[location.lineage]

    def release_bindings(self) -> None:
        """Release run-owned binding lookups while keeping descriptive topology."""
        self._bindings.clear()
        self._locations.clear()
        self._keys.clear()
        self._computed.clear()
        self._acquired.clear()

    def _provider(self, location: BoundLocation) -> str:
        if location.lineage in self._locations:
            return self._locations[location.lineage]
        if location.upstream is not None:
            result = self._visit(location.upstream)
        else:
            result = self._entry(
                {
                    "provider": location.provider.identity_payload,
                    "ids": sorted(set(location.ids))
                    if location.ids is not None
                    else None,
                },
                location.provider.provider_id,
                "provider",
                (),
            )
        self._locations[location.lineage] = result
        return result

    def _visit(self, bound: BoundInvocation) -> str:
        if bound.report_token in self._bindings:
            return self._bindings[bound.report_token]
        inputs = tuple(
            sorted(
                [
                    (name, self._provider(value))
                    for name, value in bound.locations.items()
                ]
                + [
                    (name, self._visit(value))
                    for name, value in bound.dependencies.items()
                ]
            )
        )
        # Nested operation bindings are selected uses even if no work is demanded.
        for value in bound.bindings.values():
            self._visit(value)
        payload = {
            "node": bound.node,
            "kind": bound.descriptor.kind,
            "parameters": json.loads(bound.effective_json),
            "inputs": dict(inputs),
            "outputs": dict(bound.outputs),
        }
        target = bound.descriptor.target
        target_id = (
            self._entry(
                {"target": target.identity_payload, "operation": payload},
                target.provider_id,
                "provider",
                (),
            )
            if target is not None
            else None
        )
        result = self._entry(
            payload, bound.node, bound.descriptor.kind, inputs, target_id
        )
        self._bindings[bound.report_token] = result
        return result

    def _entry(
        self,
        payload: dict,
        node: str,
        kind: Literal["metric", "operation", "provider"],
        inputs: tuple[tuple[str, str], ...],
        target: str | None = None,
    ) -> str:
        key = value_key(payload)
        if key not in self._keys:
            use_id = uuid4().hex
            self._keys[key] = use_id
            self.uses[use_id] = ResolvedUse(use_id, node, kind, inputs, target)
            if kind == "provider":
                self.providers[use_id] = ComputationObservation()
            else:
                self.computations[use_id] = ComputationObservation()
        return self._keys[key]
