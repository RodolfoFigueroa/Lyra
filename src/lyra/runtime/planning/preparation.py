"""Catalog-only request preparation, with no execution or acquisition."""

from collections.abc import Mapping, Sequence
from copy import deepcopy

from lyra.contracts.accepted import (
    ExecutionRequest,
    PreparedExecution,
    PreparedInput,
    SubmittedRequest,
)
from lyra.contracts.errors import MetricInputError
from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.parameters import prepare_parameter_uses
from lyra.runtime.planning.provider_preparation import validate_provider_selections
from lyra.sdk.interventions import Intervention, accept_interventions


class PreparationError(MetricInputError):
    """Invalid public request, rejected before execution side effects."""


def prepare_execution(
    catalog: CompiledCatalog,
    request: ExecutionRequest | JobCreateRequest | Mapping[str, object],
    *,
    allow_private: bool = False,
    native_interventions: Sequence[Intervention] | None = None,
    normalized_uses: Mapping[str, dict] | None = None,
) -> PreparedExecution:
    """Validate and pin a request using the live catalog.

    Public roots are required unless development execution allows private metrics.

    Returns:
        An independent binding retaining unresolved IDs for execution.

    Raises:
        PreparationError: If identity, selections, or parameters are invalid.
    """
    if isinstance(request, ExecutionRequest | JobCreateRequest):
        raw = request.model_dump(exclude={"interventions"})
        # Keep the current actions and the distinction between omitted and empty add.
        if request.interventions or "interventions" in request.model_fields_set:
            raw["interventions"] = request.interventions
    else:
        raw = dict(request)
    metric = str(raw.get("metric", "<request>"))
    try:
        parsed = ExecutionRequest.model_validate(deepcopy(raw))
        return _prepare(
            catalog,
            parsed,
            allow_private=allow_private,
            native_interventions=native_interventions,
            normalized_uses=normalized_uses,
        )
    except (ValueError, TypeError) as exc:
        raise PreparationError(metric, "request", str(exc)) from exc


def _prepare(
    catalog: CompiledCatalog,
    request: ExecutionRequest,
    *,
    allow_private: bool,
    native_interventions: Sequence[Intervention] | None,
    normalized_uses: Mapping[str, dict] | None,
) -> PreparedExecution:
    node_name = request.metric
    if node_name not in catalog.accepted_sources or (
        not allow_private and not catalog.graph.nodes[node_name].node.is_public_metric
    ):
        message = "Unknown metric" if allow_private else "Unknown public metric"
        raise ValueError(message)
    sources = catalog.accepted_sources[node_name]
    validate_provider_selections(
        request.inputs,
        {
            name: catalog.graph.nodes[node_name]
            .node.location_inputs[name]
            .contract.family.collection
            for name in sources
        },
        sources,
    )
    inputs = []
    for name, selection in request.inputs.items():
        binding = sources[name].get(selection.provider)
        if binding is None:
            message = f"Input {name!r}: unsupported source provider"
            raise ValueError(message)
        inputs.append(PreparedInput(name, tuple(selection.ids), binding))
    validate_execution_plan(
        catalog.graph,
        node_name,
        {item.name: item.binding.operations for item in inputs},
        catalog.dependency_routes,
    )
    effective = prepare_parameter_uses(
        catalog,
        node_name,
        request.parameters,
        inputs,
        normalized_uses=normalized_uses,
    )
    uses, dependency_routes = effective.uses, effective.routes
    parameters = uses["root"]
    original_request, targets = _capture_request(
        catalog, request, parameters, native_interventions
    )
    revisions = catalog.revisions.plan(
        node_name,
        tuple((item.binding.source, item.binding.operations) for item in inputs),
        tuple((route.source, route.operations) for route in dependency_routes.values()),
    )
    root = catalog.graph.nodes[node_name].node
    if root.kind != "metric":
        message = "Accepted root must be a metric"
        raise ValueError(message)
    return PreparedExecution(
        request=original_request,
        intervention_targets=targets,
        node=node_name,
        inputs=tuple(inputs),
        parameters=parameters,
        parameter_uses=uses,
        dependency_routes=dependency_routes,
        revisions=revisions,
        computation_identity=catalog.computation.plan(
            node_name, tuple(inputs), dependency_routes, uses
        ),
        build_revision=catalog.build_revision,
        outputs=root.outputs,
        locations=tuple(
            contract
            for contract in (
                tuple(p.declaration.contract for p in catalog.providers.values())
            )
            if contract.reference in {b.contract for b in revisions.bindings}
        ),
        has_parameters=catalog.graph.nodes[node_name].node.parameters_schema
        is not None,
        description=catalog.graph.nodes[node_name].node.description,
    )


def _capture_request(
    catalog: CompiledCatalog,
    request: ExecutionRequest,
    parameters: dict,
    native_interventions: Sequence[Intervention] | None,
) -> tuple[SubmittedRequest, dict[str, ProviderDeclaration]]:
    if native_interventions is not None and (
        "interventions" in request.model_fields_set
        or not isinstance(native_interventions, Sequence)
        or isinstance(native_interventions, str | bytes | bytearray)
        or not all(isinstance(value, Intervention) for value in native_interventions)
    ):
        msg = "Supply native interventions as a separate sequence of Intervention"
        raise ValueError(msg)
    values = (
        request.interventions if native_interventions is None else native_interventions
    )
    targets = {}
    schemas = {}
    for value in values:
        provider = catalog.provider_registry.get(value.catalog)
        if provider is None:
            msg = f"Unknown intervention catalog: {value.catalog!r}"
            raise ValueError(msg)
        if not provider.membership_capable:
            msg = f"Intervention target {value.catalog!r} does not support existing_ids"
            raise ValueError(msg)
        targets[value.catalog] = provider.declaration
        schemas[value.catalog] = provider.schema
    original_request = SubmittedRequest(
        metric=request.metric,
        parameters=parameters,
        inputs=request.inputs,
        interventions=list(accept_interventions(values, schemas)),
    )
    return original_request, targets
