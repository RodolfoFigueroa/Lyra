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

from lyra.contracts.interventions import (
    AcceptedIntervention,
    InterventionSpec,
    validate_intervention_catalogs,
)
from lyra.contracts.locations import (
    LocationContract,
    LocationContractReference,
    LocationSelection,
)
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.providers import ProviderDeclaration
from lyra.contracts.revisions import BuildRevision, RevisionBinding, RevisionPlan
from lyra.contracts.value_identity import check_json_value

_REVISION_PLAN_ADAPTER = TypeAdapter(RevisionPlan)


class ExecutionRequest(BaseModel):
    """Canonical public selection before defaults are applied."""

    model_config = ConfigDict(extra="forbid", strict=True)
    metric: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    interventions: list[InterventionSpec] = Field(default_factory=list)

    @field_validator("interventions")
    @classmethod
    def validate_interventions(
        cls, values: list[InterventionSpec]
    ) -> list[InterventionSpec]:
        """Return independently captured sets with distinct catalogs."""
        return deepcopy(validate_intervention_catalogs(values))


class SubmittedRequest(BaseModel):
    """Validated caller selections and request values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("parameters", "inputs", "interventions", mode="before")
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent values captured at construction."""
        return deepcopy(value)

    metric: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, LocationSelection] = Field(default_factory=dict)
    interventions: list[AcceptedIntervention] = Field(default_factory=list)

    def submission_payload(self) -> dict:
        """Return public fields for validation against the worker's registration."""
        payload = self.model_dump(exclude={"interventions"})
        payload["interventions"] = [
            value.submission_payload() for value in self.interventions
        ]
        return payload


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
    """A live catalog's resolved bindings and declarations for execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator(
        "request",
        "inputs",
        "parameters",
        "parameter_uses",
        "dependency_routes",
        "revisions",
        "outputs",
        "locations",
        "intervention_targets",
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
    dependency_routes: dict[str, AcceptedSource]
    revisions: RevisionPlan
    computation_identity: str = Field(min_length=1)
    build_revision: BuildRevision | None
    outputs: dict[str, OutputMetadata] = Field(min_length=1)
    locations: tuple[LocationContract, ...]
    intervention_targets: dict[str, ProviderDeclaration] = Field(default_factory=dict)
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

    def required_scopes(self) -> dict[str, RevisionBinding]:
        """Return the selected provider identities including intervention targets."""
        scopes = {item.contract.collection: item for item in self.revisions.bindings}
        for name, provider in self.intervention_targets.items():
            scopes[name] = RevisionBinding(
                provider.contract.reference, provider.contract.data_revision
            )
        return scopes

    @property
    def output_metadata(self) -> dict[str, OutputMetadata]:
        """Detached descriptive output declarations for inspection."""
        return deepcopy(self.outputs)

    @property
    def metric(self) -> str:
        """Submitted public metric identity."""
        return self.request.metric

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
        _validate_intervention_targets(self)
        return self


def _validate_intervention_targets(plan: PreparedExecution) -> None:
    catalogs = [value.catalog for value in plan.request.interventions]
    if (
        len(catalogs) != len(set(catalogs))
        or set(catalogs) != plan.intervention_targets.keys()
    ):
        msg = "Interventions must have exactly one pinned declaration per catalog"
        raise ValueError(msg)
    for value in plan.request.interventions:
        target = plan.intervention_targets[value.catalog]
        if target.id != value.catalog or not target.membership_capable:
            msg = f"Incompatible pinned intervention target: {value.catalog!r}"
            raise ValueError(msg)
        if value.addition_schema != target.contract.family.structure.output:
            msg = f"Addition schema differs from pinned target: {value.catalog!r}"
            raise ValueError(msg)
    for binding in plan.revisions.bindings:
        target = plan.intervention_targets.get(binding.contract.collection)
        if target is not None and (
            target.contract.reference != binding.contract
            or target.contract.data_revision != binding.revision
            or target.contract not in plan.locations
        ):
            msg = "Intervention scope differs from the pinned computation scope"
            raise ValueError(msg)


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
    _validate_dependency_routes(plan, revisions)


def _validate_dependency_routes(
    plan: PreparedExecution, revisions: dict[LocationContractReference, str | None]
) -> None:
    for route in plan.dependency_routes.values():
        if route.source not in revisions or route.target not in revisions:
            message = "Missing pinned dependency route revision"
            raise ValueError(message)
        if not route.operations and route.source != route.target:
            message = "Native dependency binding requires identical contracts"
            raise ValueError(message)


class WorkerRequest(BaseModel):
    """Normalized values accepted for execution by the matching worker build."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request: SubmittedRequest
    parameters: dict[str, JsonValue]
    parameter_uses: dict[str, dict[str, JsonValue]]
    build_revision: BuildRevision | None
    provider_scopes: dict[str, RevisionBinding]
    has_parameters: bool

    @classmethod
    def from_plan(cls, plan: PreparedExecution) -> Self:
        """Capture request values and all required provider identities.

        Returns:
            A detached request for the worker.
        """
        return cls(
            request=plan.request,
            parameters=plan.parameters,
            parameter_uses=plan.parameter_uses,
            build_revision=plan.build_revision,
            provider_scopes=plan.required_scopes(),
            has_parameters=plan.has_parameters,
        ).model_copy(deep=True)

    @property
    def metric(self) -> str:
        """The public metric selected by the caller."""
        return self.request.metric

    @model_validator(mode="after")
    def check_parameters(self) -> Self:
        """Require consistent effective parameter values.

        Returns:
            The validated request.

        Raises:
            ValueError: If the root values differ.
        """
        if (
            self.parameter_uses.get("root") != self.parameters
            or self.parameters != self.request.parameters
        ):
            msg = "Effective root parameters disagree with acceptance"
            raise ValueError(msg)
        return self
