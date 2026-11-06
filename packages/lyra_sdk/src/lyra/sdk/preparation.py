"""Catalog-only public request acceptance, with no execution or acquisition."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from jsonschema.exceptions import ValidationError as SchemaValidationError
from lyra.sdk.accepted import (
    CapturedContracts,
    ExecutionRequest,
    PreparedExecution,
    PreparedInput,
    SubmittedRequest,
)
from lyra.sdk.catalog import CompiledCatalog
from lyra.sdk.computation import validate_cache_revision
from lyra.sdk.errors import MetricInputError
from lyra.sdk.schema import (
    check_json_value,
    resolve_schema_reference,
    subschema_validator,
)


class PreparationError(MetricInputError):
    """Invalid public request, rejected before execution side effects."""


def _defaults(root: dict, schema: object, value: object) -> object:
    if not isinstance(schema, dict):
        return value
    if "$ref" in schema:
        value = _defaults(root, resolve_schema_reference(root, schema["$ref"]), value)
    for branch in schema.get("allOf", []):
        value = _defaults(root, branch, value)
    for keyword in ("anyOf", "oneOf"):
        for branch in schema.get(keyword, []):
            if subschema_validator(root, branch).is_valid(value):
                value = _defaults(root, branch, value)
                break
    return _container_defaults(root, schema, value)


def _container_defaults(root: dict, schema: dict, value: object) -> object:
    if isinstance(value, dict):
        for name, field in schema.get("properties", {}).items():
            if name not in value and "default" in field:
                value[name] = deepcopy(field["default"])
            if name in value:
                value[name] = _defaults(root, field, value[name])
        for name in value.keys() - schema.get("properties", {}).keys():
            value[name] = _defaults(
                root, schema.get("additionalProperties", True), value[name]
            )
    elif isinstance(value, list):
        value = [_defaults(root, schema.get("items", True), item) for item in value]
    return value


def prepare_execution(
    catalog: CompiledCatalog, request: ExecutionRequest | Mapping[str, object]
) -> PreparedExecution:
    """Validate and pin a public request using only compiled metadata.

    Returns:
        An immutable binding retaining unresolved IDs for execution.

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
        return _prepare(catalog, parsed)
    except (ValueError, TypeError, SchemaValidationError) as exc:
        raise PreparationError(
            metric, f"edition={raw.get('edition')!r}", str(exc)
        ) from exc


def _prepare(catalog: CompiledCatalog, request: ExecutionRequest) -> PreparedExecution:
    node_name = catalog.editions.resolve(request.metric, edition=request.edition)
    if node_name not in catalog.accepted_sources:
        message = "Unknown public metric"
        raise ValueError(message)
    node = catalog.graph.nodes[node_name].node
    sources = catalog.accepted_sources[node_name]
    if request.inputs.keys() != sources.keys():
        message = f"Expected inputs {sorted(sources)}"
        raise ValueError(message)
    inputs = []
    for name, selection in request.inputs.items():
        binding = sources[name].get((selection.collection, selection.unit_type))
        if binding is None:
            message = f"Input {name!r}: unsupported source collection/unit type"
            raise ValueError(message)
        inputs.append(PreparedInput(name, tuple(selection.ids), binding))
    schema: dict[str, Any] = node.request_schema
    parameter_schema = schema["properties"].get(
        "parameters", {"type": "object", "additionalProperties": False}
    )
    original_request = SubmittedRequest(
        **request.model_dump(), supplied_fields=request.model_fields_set
    )
    parameters = deepcopy(request.parameters)
    check_json_value(parameters, "parameters")
    subschema_validator(schema, parameter_schema).validate(parameters)
    effective = _defaults(schema, parameter_schema, parameters)
    subschema_validator(schema, parameter_schema).validate(effective)
    validate_cache_revision(request.cache_revision)
    revisions = catalog.revisions.plan(
        node_name,
        tuple((item.binding.source, item.binding.operations) for item in inputs),
    )
    return PreparedExecution(
        request=original_request,
        node=node_name,
        inputs=tuple(inputs),
        parameters=parameters,
        revisions=revisions,
        structural_namespace=catalog.structural_namespace,
        contracts=CapturedContracts(
            nodes={
                name: catalog.graph.nodes[name].node
                for name in _planned_nodes(catalog, node_name, inputs)
            },
            locations=tuple(
                contract
                for contract in (
                    catalog.location_provider.declaration.contracts
                    if catalog.location_provider
                    else ()
                )
                if contract.reference in {b.contract for b in revisions.bindings}
            ),
        ),
    )


def _planned_nodes(
    catalog: CompiledCatalog, node: str, inputs: list[PreparedInput]
) -> tuple[str, ...]:
    """Collect declared calculation and selected route nodes without executing.

    Returns:
        Sorted concrete node identities in the accepted closure.
    """
    return catalog.graph.dependency_closure(
        [node, *(op for item in inputs for op in item.binding.operations)]
    )
