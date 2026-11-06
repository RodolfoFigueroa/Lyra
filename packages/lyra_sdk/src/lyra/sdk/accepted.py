"""Owned typed acceptance snapshots, independent of catalog compilation."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Self

from lyra.sdk.location_contracts import LocationContract, LocationContractReference
from lyra.sdk.models.cache_revision import CacheRevision
from lyra.sdk.models.location_payload import LocationSelection
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.plugin import NodeManifest
from lyra.sdk.revisions import RevisionPlan
from lyra.sdk.schema import check_json_value
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_serializer,
    model_validator,
)

_REVISION_PLAN_ADAPTER = TypeAdapter(RevisionPlan)


class OwnedModel(BaseModel):
    """Freeze fields and detach mutable inputs and public field views."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def own_values(cls, value: object) -> object:
        """Return an owned copy of incoming containers."""
        return deepcopy(value)

    def __getattribute__(self, name: str) -> Any:  # ruff: ignore[any-type] - preserve BaseModel dynamic attribute protocol
        """Return detached views of mutable model fields."""
        value = super().__getattribute__(name)
        if isinstance(value, OwnedModel):
            return value
        if name in type(self).model_fields and isinstance(
            value, (dict, list, BaseModel)
        ):
            return deepcopy(value)
        if name in type(self).model_fields and isinstance(value, tuple):
            return deepcopy(value)
        return value


class ExecutionRequest(BaseModel):
    """Canonical public selection before defaults are applied."""

    model_config = ConfigDict(extra="forbid", strict=True)
    metric: str
    edition: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    cache_revision: CacheRevision | None = None


class SubmittedRequest(OwnedModel):
    """Validated request values with durable caller field presence."""

    metric: str
    edition: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    cache_revision: CacheRevision | None = None
    supplied_fields: frozenset[str]

    @field_serializer("supplied_fields")
    @staticmethod
    def serialize_presence(value: frozenset[str]) -> list[str]:
        """Return supplied field names in stable order."""
        return sorted(value)

    @model_validator(mode="after")
    def validate_presence(self) -> Self:
        """Validate submitted field presence.

        Returns:
            This owned request.

        Raises:
            ValueError: If field presence contradicts captured values.
        """
        if (
            not self.supplied_fields <= ExecutionRequest.model_fields.keys()
            or "metric" not in self.supplied_fields
        ):
            message = "Invalid submitted field presence"
            raise ValueError(message)
        for name in ExecutionRequest.model_fields:
            if name not in self.supplied_fields and getattr(self, name) not in (
                None,
                {},
            ):
                message = "Omitted submitted field has a value"
                raise ValueError(message)
        return self


@dataclass(frozen=True)
class AcceptedSource:
    """Concrete public selection binding and its native input route."""

    source: LocationContractReference
    target: LocationContractReference
    operations: tuple[str, ...]


@dataclass(frozen=True)
class PreparedInput:
    """Original ordered IDs and their exact concrete canonical binding."""

    name: str
    ids: tuple[str, ...]
    binding: AcceptedSource


class CapturedContracts(OwnedModel):
    """Declarations needed to honor the accepted computation."""

    nodes: dict[str, NodeManifest]
    locations: tuple[LocationContract, ...]


class PreparedExecution(OwnedModel):
    """The sole validated acceptance contract for storage and execution."""

    request: SubmittedRequest
    node: str = Field(min_length=1)
    inputs: tuple[PreparedInput, ...]
    parameters: dict[str, JsonValue]
    revisions: RevisionPlan
    computation_identity: str = Field(min_length=1)
    contracts: CapturedContracts

    @field_serializer("revisions")
    @staticmethod
    def serialize_revisions(value: RevisionPlan) -> dict:
        """Return revision arrays for both Python and JSON views."""
        result = _REVISION_PLAN_ADAPTER.dump_python(value, mode="json")
        for bindings in result.values():
            bindings.sort(key=lambda item: json.dumps(item, sort_keys=True))
        return result

    @property
    def output_metadata(self) -> dict[str, OutputMetadata]:
        """Detached root output declarations from accepted contracts.

        Raises:
            ValueError: If an unvalidated plan has a nonmetric root.
        """
        root = self.contracts.nodes[self.node]
        if root.kind != "metric":
            message = "Accepted root must be a metric"
            raise ValueError(message)
        return root.outputs

    @property
    def metric(self) -> str:
        """Submitted public metric identity."""
        return self.request.metric

    @property
    def edition(self) -> str | None:
        """Submitted edition."""
        return self.request.edition

    @property
    def cache_revision(self) -> str | None:
        """Caller cache revision."""
        return self.request.cache_revision

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        """Validate the complete binding without a live catalog.

        Returns:
            This validated plan.

        Raises:
            ValueError: If captured inputs or bindings disagree.
        """
        check_json_value(self.parameters, "parameters")
        check_json_value(self.request.parameters, "submitted parameters")
        names = [item.name for item in self.inputs]
        if len(set(names)) != len(names) or set(names) != self.request.inputs.keys():
            message = "Input bindings disagree with original request"
            raise ValueError(message)
        _validate_bindings(self, self.request, self.contracts)
        return self


def _validate_bindings(
    plan: PreparedExecution, request: SubmittedRequest, metadata: CapturedContracts
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
    nodes = metadata.nodes
    if not isinstance(nodes, dict) or plan.node not in nodes:
        message = "Missing concrete metric metadata"
        raise ValueError(message)
    _validate_metadata(plan, nodes)
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


def _validate_metadata(plan: PreparedExecution, nodes: dict[str, NodeManifest]) -> None:
    declarations = nodes
    _validate_declarations(plan, declarations)
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
                or node.kind != "location_operation"
                or not node.canonical
                or current
                not in {
                    value.contract.reference for value in node.location_inputs.values()
                }
            ):
                message = "Invalid captured route hop"
                raise ValueError(message)
            current = node.output_contract.reference
        target = declarations[plan.node].location_inputs.get(item.name)
        if (
            current != item.binding.target
            or target is None
            or target.contract.reference != current
        ):
            message = "Captured route target disagrees with metric input"
            raise ValueError(message)


def _validate_declarations(
    plan: PreparedExecution, declarations: dict[str, NodeManifest]
) -> None:
    metric = declarations[plan.node]
    if metric.kind != "metric":
        message = "Accepted root must be a metric"
        raise ValueError(message)
    if (metric.family or metric.name, metric.edition) != (
        plan.metric,
        plan.edition,
    ) or not metric.is_public_metric:
        message = "Concrete metric metadata disagrees with binding"
        raise ValueError(message)
    if set(metric.location_inputs) != {item.name for item in plan.inputs}:
        message = "Missing metric input bindings"
        raise ValueError(message)
    revisions = {
        binding.contract: binding.revision for binding in plan.revisions.bindings
    }
    for node in declarations.values():
        contracts = [value.contract for value in node.location_inputs.values()]
        if node.kind == "location_operation":
            contracts.append(node.output_contract)
        for contract in contracts:
            if (
                contract.reference not in revisions
                or revisions[contract.reference] != contract.data_revision
            ):
                message = "Missing or inconsistent pinned declaration revision"
                raise ValueError(message)
