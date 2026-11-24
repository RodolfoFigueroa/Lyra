"""Public retained result descriptions without storage or execution dependencies."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import DatasetScope
from lyra.contracts.references import JobId, RetrievalName
from lyra.contracts.reports import ReportOutput
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class LocationInputDescription(StrictBaseModel):
    """Captured schema, scope roles, and availability of one root location input."""

    kind: Literal["provider", "correspondence"]
    table_schema: OutputMetadata
    provider_scope: DatasetScope
    source_scope: DatasetScope | None = None
    availability: Literal["available", "not_materialized"]

    @model_validator(mode="after")
    def validate_source_scope(self) -> Self:
        """Require an immediate source scope exactly for correspondences.

        Returns:
            The validated input description.

        Raises:
            ValueError: If the source scope does not match the input kind.
        """
        if (self.kind == "correspondence") != (self.source_scope is not None):
            msg = "Only correspondence inputs require a source scope"
            raise ValueError(msg)
        return self


class ExecutionMetadata(StrictBaseModel):
    """Effective root parameters and retained table descriptions for an execution."""

    metric_name: str
    parameters: JsonObject
    outputs: dict[str, ReportOutput]
    location_inputs: dict[RetrievalName, LocationInputDescription] = Field(
        default_factory=dict
    )


class ResultDescription(ExecutionMetadata):
    """Complete caller-owned root metadata addressed by job ID."""

    job_id: JobId


class CompleteResultDescription(StrictBaseModel):
    """Inspection containing the entire retained description."""

    kind: Literal["complete"] = "complete"
    description: ResultDescription


class ResultDescriptionPreview(StrictBaseModel):
    """Fixed summary linking to the complete authenticated JSON download."""

    kind: Literal["preview"] = "preview"
    job_id: JobId
    omitted: tuple[Literal["description"]] = ("description",)
    download_url: str


class TableDownload(StrictBaseModel):
    """Authenticated Arrow IPC stream address for one validated retained output."""

    job_id: JobId
    output_name: str
    download_url: str


class LocationInputDownload(StrictBaseModel):
    """Authenticated Arrow address for one validated retained location input."""

    job_id: JobId
    input_name: RetrievalName
    download_url: str


ResultDescriptionInspection = Annotated[
    CompleteResultDescription | ResultDescriptionPreview, Field(discriminator="kind")
]
