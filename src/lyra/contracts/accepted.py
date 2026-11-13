"""Owned typed acceptance snapshots, independent of catalog compilation."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from lyra.contracts.locations import (
    LocationContract,
    LocationContractReference,
    LocationSelection,
)
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.revisions import BuildRevision, CacheRevision, RevisionPlan
from lyra.contracts.schema import check_json_value

_REVISION_PLAN_ADAPTER = TypeAdapter(RevisionPlan)


class ExecutionRequest(BaseModel):
    """Canonical public selection before defaults are applied."""

    model_config = ConfigDict(extra="forbid", strict=True)
    metric: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    cache_revision: CacheRevision | None = None


class SubmittedRequest(BaseModel):
    """Validated caller selections and request values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("parameters", "inputs", mode="before")
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    metric: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    cache_revision: CacheRevision | None = None


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


class PreparedExecution(BaseModel):
    """The sole validated acceptance contract for storage and execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator(
        "request",
        "inputs",
        "parameters",
        "parameter_uses",
        "revisions",
        "outputs",
        "locations",
        mode="before",
    )
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    request: SubmittedRequest
    node: str = Field(min_length=1)
    inputs: tuple[PreparedInput, ...]
    parameters: dict[str, JsonValue]
    parameter_uses: dict[str, dict[str, JsonValue]]
    revisions: RevisionPlan
    computation_identity: str = Field(min_length=1)
    build_revision: BuildRevision | None
    outputs: dict[str, OutputMetadata] = Field(min_length=1)
    locations: tuple[LocationContract, ...]
    has_parameters: bool
    description: str

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
        """Detached descriptive output declarations for inspection."""
        return deepcopy(self.outputs)

    @property
    def metric(self) -> str:
        """Submitted public metric identity."""
        return self.request.metric

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
        check_json_value(self.parameter_uses, "parameter uses")
        if (
            self.parameter_uses.get("root") != self.parameters
            or self.request.parameters != self.parameters
        ):
            message = "Effective root parameters disagree with acceptance"
            raise ValueError(message)
        check_json_value(self.request.parameters, "submitted parameters")
        names = [item.name for item in self.inputs]
        if len(set(names)) != len(names) or set(names) != self.request.inputs.keys():
            message = "Input bindings disagree with original request"
            raise ValueError(message)
        _validate_bindings(self, self.request)
        return self


def _validate_bindings(plan: PreparedExecution, request: SubmittedRequest) -> None:
    revisions = {}
    for binding in plan.revisions.bindings:
        if (
            binding.contract in revisions
            and revisions[binding.contract] != binding.revision
        ):
            message = "Conflicting pinned revisions"
            raise ValueError(message)
        revisions[binding.contract] = binding.revision
    if plan.node != plan.metric:
        message = "Metric identity disagrees with binding"
        raise ValueError(message)
    for item in plan.inputs:
        selection = request.inputs[item.name]
        source = item.binding.source
        if (selection.provider, tuple(selection.ids)) != (
            source.collection,
            item.ids,
        ):
            message = "Original selection disagrees with binding"
            raise ValueError(message)
        if source not in revisions or item.binding.target not in revisions:
            message = "Missing pinned input revision"
            raise ValueError(message)
        if not item.binding.operations and source != item.binding.target:
            message = "Native binding requires identical contracts"
            raise ValueError(message)
