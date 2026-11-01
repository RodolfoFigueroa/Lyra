"""Internal persistence of accepted bindings, independent of a live catalog."""

import json
from typing import Annotated

from lyra.sdk.computation import CacheRevision
from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.models.plugin import NodeManifest
from lyra.sdk.preparation import ExecutionRequest, PreparedExecution
from lyra.sdk.schema import check_json_value
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class _StoredPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    metric: Annotated[str, Field(min_length=1)]
    edition: str | None
    node: Annotated[str, Field(min_length=1)]
    inputs: tuple
    parameters_json: str
    revisions: dict[str, JsonValue]
    structural_namespace: Annotated[str, Field(min_length=1)]
    cache_revision: CacheRevision | None
    metadata_json: str
    request_json: str


_PLAN = TypeAdapter(PreparedExecution)


def serialize_bound_plan(plan: PreparedExecution) -> str:
    """Return validated JSON containing only captured acceptance metadata."""
    payload = _PLAN.dump_json(plan).decode()
    deserialize_bound_plan(payload)
    return payload


def deserialize_bound_plan(payload: str) -> PreparedExecution:
    """Reconstruct immutable bindings without consulting declarations or resources.

    Returns:
        The captured execution binding.

    Raises:
        TypeError: If embedded metadata is not an object.
        ValueError: If stored fields or their binding relationships are invalid.
    """
    _StoredPlan.model_validate_json(payload)
    plan = _PLAN.validate_json(payload, strict=True)
    request = ExecutionRequest.model_validate_json(plan.request_json)
    parameters = json.loads(plan.parameters_json)
    metadata = json.loads(plan.metadata_json)
    check_json_value(parameters, "parameters")
    check_json_value(metadata, "metadata")
    if not isinstance(parameters, dict) or not isinstance(metadata, dict):
        message = "Expected parameter and metadata objects"
        raise TypeError(message)
    if (request.metric, request.edition, request.cache_revision) != (
        plan.metric,
        plan.edition,
        plan.cache_revision,
    ):
        message = "Original request identity disagrees with binding"
        raise ValueError(message)
    names = [item.name for item in plan.inputs]
    if len(set(names)) != len(names) or set(names) != request.inputs.keys():
        message = "Input bindings disagree with original request"
        raise ValueError(message)
    _validate_bindings(plan, request, metadata)
    return plan


def _validate_bindings(
    plan: PreparedExecution, request: ExecutionRequest, metadata: dict
) -> None:
    revisions = {}
    for binding in plan.revisions.bindings:
        if (
            binding.contract in revisions
            and revisions[binding.contract] != binding.revision
        ):
            message = "Conflicting pinned revisions"
            raise ValueError(message)
        revisions[binding.contract] = binding.revision
    nodes = metadata.get("nodes")
    if not isinstance(nodes, dict) or plan.node not in nodes:
        message = "Missing concrete metric metadata"
        raise ValueError(message)
    _validate_metadata(plan, nodes, metadata)
    for item in plan.inputs:
        selection = request.inputs[item.name]
        source = item.binding.source
        if (selection.collection, selection.unit_type, tuple(selection.ids)) != (
            source.collection,
            source.unit_type,
            item.ids,
        ):
            message = "Original selection disagrees with binding"
            raise ValueError(message)
        if source not in revisions or item.binding.target not in revisions:
            message = "Missing pinned input revision"
            raise ValueError(message)
        if any(operation not in nodes for operation in item.binding.operations):
            message = "Missing concrete operation metadata"
            raise ValueError(message)
        if not item.binding.operations and source != item.binding.target:
            message = "Native binding requires identical contracts"
            raise ValueError(message)


def _validate_metadata(plan: PreparedExecution, nodes: dict, metadata: dict) -> None:
    declarations = {
        name: NodeManifest.model_validate(value) for name, value in nodes.items()
    }
    _validate_declarations(plan, declarations)
    for value in metadata.get("locations", []):
        LocationContract.model_validate(value)
    for name, node in declarations.items():
        if name != node.name:
            message = "Concrete node identity disagrees with metadata"
            raise ValueError(message)
    for item in plan.inputs:
        current = item.binding.source
        for operation in item.binding.operations:
            node = declarations.get(operation)
            if (
                node is None
                or not node.canonical
                or node.output_contract is None
                or current
                not in {c.reference for c in node.location_contracts.values()}
            ):
                message = "Invalid captured route hop"
                raise ValueError(message)
            current = node.output_contract.reference
        target = declarations[plan.node].location_contracts.get(item.name)
        if (
            current != item.binding.target
            or target is None
            or target.reference != current
        ):
            message = "Captured route target disagrees with metric input"
            raise ValueError(message)


def _validate_declarations(
    plan: PreparedExecution, declarations: dict[str, NodeManifest]
) -> None:
    metric = declarations[plan.node]
    if (metric.family or metric.name, metric.edition) != (
        plan.metric,
        plan.edition,
    ) or not metric.is_public_metric:
        message = "Concrete metric metadata disagrees with binding"
        raise ValueError(message)
    if set(metric.location_contracts) != {item.name for item in plan.inputs}:
        message = "Missing metric input bindings"
        raise ValueError(message)
    revisions = {
        binding.contract: binding.revision for binding in plan.revisions.bindings
    }
    for node in declarations.values():
        contracts = list(node.location_contracts.values())
        if node.output_contract is not None:
            contracts.append(node.output_contract)
        for contract in contracts:
            if (
                contract.reference not in revisions
                or revisions[contract.reference] != contract.data_revision
            ):
                message = "Missing or inconsistent pinned declaration revision"
                raise ValueError(message)
