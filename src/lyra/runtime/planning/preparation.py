"""Catalog-only request preparation, with no execution or acquisition."""

from collections.abc import Mapping
from copy import deepcopy

from lyra.contracts.accepted import (
    ExecutionRequest,
    PreparedExecution,
    PreparedInput,
    SubmittedRequest,
)
from lyra.contracts.errors import MetricInputError
from lyra.runtime.planning._execution_plan import (
    validate_execution_plan,
)
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.computation import validate_cache_revision
from lyra.runtime.planning.parameters import prepare_parameter_uses
from lyra.runtime.planning.provider_preparation import validate_provider_selections


class PreparationError(MetricInputError):
    """Invalid public request, rejected before execution side effects."""


def prepare_execution(
    catalog: CompiledCatalog,
    request: ExecutionRequest | Mapping[str, object],
    *,
    allow_private: bool = False,
    normalized_uses: Mapping[str, dict] | None = None,
) -> PreparedExecution:
    """Validate and pin a request using the live catalog.

    Public roots are required unless development execution allows private metrics.

    Returns:
        An independent binding retaining unresolved IDs for execution.

    Raises:
        PreparationError: If identity, selections, or parameters are invalid.
    """
    raw = (
        request.model_dump(exclude_unset=True)
        if isinstance(request, ExecutionRequest)
        else dict(request)
    )
    metric = str(raw.get("metric", "<request>"))
    try:
        parsed = ExecutionRequest.model_validate(deepcopy(raw))
        return _prepare(
            catalog,
            parsed,
            allow_private=allow_private,
            normalized_uses=normalized_uses,
        )
    except (ValueError, TypeError) as exc:
        raise PreparationError(metric, "request", str(exc)) from exc


def _prepare(
    catalog: CompiledCatalog,
    request: ExecutionRequest,
    *,
    allow_private: bool,
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
    if normalized_uses is None:
        validate_execution_plan(
            catalog.graph,
            node_name,
            {item.name: item.binding.operations for item in inputs},
        )
    uses = prepare_parameter_uses(
        catalog, node_name, request.parameters, inputs, normalized_uses=normalized_uses
    )
    parameters = uses["root"]
    original_request = SubmittedRequest.model_validate(
        request.model_dump() | {"parameters": parameters}
    )
    validate_cache_revision(request.cache_revision)
    revisions = catalog.revisions.plan(
        node_name,
        tuple((item.binding.source, item.binding.operations) for item in inputs),
    )
    root = catalog.graph.nodes[node_name].node
    if root.kind != "metric":
        message = "Accepted root must be a metric"
        raise ValueError(message)
    return PreparedExecution(
        request=original_request,
        node=node_name,
        inputs=tuple(inputs),
        parameters=parameters,
        parameter_uses=uses,
        revisions=revisions,
        computation_identity=catalog.computation.plan(node_name, tuple(inputs)),
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
