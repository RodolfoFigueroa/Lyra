"""Complete successful execution descriptions, independent of executable plugins."""

from copy import deepcopy
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from lyra.contracts.accepted import SubmittedRequest
from lyra.contracts.origins import Origin
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import DatasetScope, EffectiveScope
from lyra.contracts.references import JobId
from lyra.contracts.revision_tokens import BuildRevision, ImplementationRevision
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.value_identity import check_json_value, same_json

LocalName = Annotated[str, Field(min_length=1)]
NodeOutcome = Literal["executed", "reused", "mixed", "skipped"]
StageOutcome = Literal["executed", "skipped"]
SelectorOutcome = Literal["executed", "skipped", "not_applicable"]


class ReportOutput(StrictBaseModel):
    """Named metric schema and concrete ownership, including empty outputs."""

    scope: Literal["location", "unpartitioned"]
    table_schema: OutputMetadata
    owner_scope: DatasetScope | None

    @model_validator(mode="after")
    def validate_owner(self) -> Self:
        """Return a description with ownership matching its scope.

        Raises:
            ValueError: If location ownership is missing or unexpected.
        """
        if (self.scope == "location") != (self.owner_scope is not None):
            msg = "Output ownership must match its scope"
            raise ValueError(msg)
        return self


class DataBinding(StrictBaseModel):
    """A named input's producing use and concrete location scope."""

    source_use_id: LocalName
    output_name: LocalName | None
    provider_scope: DatasetScope | None


class ParameterBinding(StrictBaseModel):
    """Direct forwarding of one complete named parameter."""

    source_use_id: LocalName
    source_parameter: LocalName
    target_use_id: LocalName
    target_parameter: LocalName


class InterventionValidation(StrictBaseModel):
    """A completely validated set and whether this run queried base membership."""

    scope: EffectiveScope
    membership_lookup: StageOutcome


class OperationReport(StrictBaseModel):
    """Declared target and independently observed operation stages."""

    target_scope: DatasetScope
    target_adapter: Literal["parquet", "postgis"] | None
    selector: SelectorOutcome
    target_query: SelectorOutcome
    mapping: StageOutcome

    @model_validator(mode="after")
    def validate_applicability(self) -> Self:
        """Return consistent selector and query applicability.

        Raises:
            ValueError: If only one selector-dependent stage applies.
        """
        if (self.selector == "not_applicable") != (
            self.target_query == "not_applicable"
        ):
            msg = "Selector and target query must have matching applicability"
            raise ValueError(msg)
        return self


class ReportNode(StrictBaseModel):
    """One resolved use and its aggregate observed work."""

    use_id: LocalName
    node_id: LocalName
    kind: Literal["metric", "operation", "provider"]
    outcome: NodeOutcome
    parameters: dict[str, JsonValue]
    primary_input: LocalName | None
    inputs: dict[str, DataBinding]
    outputs: dict[str, ReportOutput]
    code_revision: ImplementationRevision | None
    origins: list[Origin]
    operation: OperationReport | None = None

    @field_validator(
        "parameters", "inputs", "outputs", "origins", "operation", mode="before"
    )
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return an independent capture of mutable descriptions."""
        return deepcopy(value)

    @field_validator("parameters", mode="before")
    @classmethod
    def validate_parameters(cls, value: object) -> object:
        """Return effective parameters restricted to finite JSON values."""
        check_json_value(value, "parameters")
        return value

    @field_validator("origins")
    @classmethod
    def deduplicate_origins(cls, origins: list[Origin]) -> list[Origin]:
        """Return direct declarations deduplicated by external identity."""
        return list(dict.fromkeys(origins))

    @model_validator(mode="after")
    def validate_description(self) -> Self:
        """Return a node with consistent input and operation descriptions.

        Raises:
            ValueError: If the primary input or operation details disagree.
        """
        if self.primary_input is not None and self.primary_input not in self.inputs:
            msg = "Primary input must name a resolved input"
            raise ValueError(msg)
        if (self.kind == "operation") != (self.operation is not None):
            msg = "Operation details are required exactly for operation nodes"
            raise ValueError(msg)
        if self.kind == "operation" and self.origins:
            msg = "Operations have no direct origins"
            raise ValueError(msg)
        return self


class ExecutionReport(BaseModel):
    """A complete successful execution captured as ordinary descriptive values.

    Construction detaches inputs. Return boundaries use model_copy(deep=True) to
    detach retained reports; ordinary field access returns the owned values.
    """

    model_config = ConfigDict(extra="forbid")

    request: SubmittedRequest
    job_id: JobId
    status: Literal["succeeded"]
    root_use_id: LocalName
    nodes: list[ReportNode]
    parameter_bindings: list[ParameterBinding]
    build_revision: BuildRevision | None
    intervention_validations: list[InterventionValidation] = Field(default_factory=list)

    @field_validator(
        "request",
        "nodes",
        "parameter_bindings",
        "intervention_validations",
        mode="before",
    )
    @classmethod
    def capture_values(cls, value: object) -> object:
        """Return independent descriptions at the report capture boundary."""
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_links(self) -> Self:
        """Return a complete report with valid local graph links.

        Raises:
            ValueError: If identities, named links, or root descriptions disagree.
        """
        uses = {node.use_id: node for node in self.nodes}
        if len(uses) != len(self.nodes) or self.root_use_id not in uses:
            msg = "Report requires unique use IDs and an existing root"
            raise ValueError(msg)
        root = uses[self.root_use_id]
        if (
            root.kind != "metric"
            or root.node_id != self.request.metric
            or not same_json(root.parameters, self.request.parameters)
        ):
            msg = "Request must match the root metric and effective parameters"
            raise ValueError(msg)
        accepted = {change.identity for change in self.request.interventions}
        observed = [
            validation.scope.intervention
            for validation in self.intervention_validations
        ]
        if set(observed) != accepted or len(observed) != len(accepted):
            msg = "Report requires a validation outcome for each accepted intervention"
            raise ValueError(msg)
        for node in self.nodes:
            for binding in node.inputs.values():
                source = uses.get(binding.source_use_id)
                if source is None:
                    msg = "Data binding must identify an existing use"
                    raise ValueError(msg)
                _validate_data_binding(binding, source)
        for binding in self.parameter_bindings:
            source = uses.get(binding.source_use_id)
            target = uses.get(binding.target_use_id)
            if (
                source is None
                or target is None
                or binding.source_parameter not in source.parameters
                or binding.target_parameter not in target.parameters
            ):
                msg = "Parameter binding must identify existing uses and parameters"
                raise ValueError(msg)
        return self


def _validate_data_binding(binding: DataBinding, source: ReportNode) -> None:
    if source.kind == "metric":
        if (
            binding.output_name not in source.outputs
            or binding.provider_scope is not None
        ):
            msg = "Metric binding requires a named output without provider scope"
            raise ValueError(msg)
    elif binding.output_name is not None or binding.provider_scope is None:
        msg = "Location binding requires provider scope and no named output"
        raise ValueError(msg)


class CompleteExecutionReport(StrictBaseModel):
    """Inspection containing the entire retained execution report."""

    kind: Literal["complete"] = "complete"
    report: ExecutionReport


class ExecutionReportPreview(StrictBaseModel):
    """Fixed summary linking to the complete authenticated JSON download."""

    kind: Literal["preview"] = "preview"
    job_id: JobId
    metric: str
    status: Literal["succeeded"]
    omitted: tuple[Literal["report"]] = ("report",)
    download_url: str


ExecutionReportInspection = Annotated[
    CompleteExecutionReport | ExecutionReportPreview, Field(discriminator="kind")
]
