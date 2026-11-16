"""Public retained result descriptions without storage or execution dependencies."""

from typing import Annotated, Literal

from pydantic import Field

from lyra.contracts.references import ExecutionID, ResultReference
from lyra.contracts.reports import ReportOutput
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class ExecutionMetadata(StrictBaseModel):
    """Effective root parameters and output descriptions for one execution."""

    metric_name: str
    parameters: JsonObject
    outputs: dict[str, ReportOutput]


class ResultDescription(ExecutionMetadata):
    """Complete caller-owned root metadata addressed by durable identifiers."""

    result_ref: ResultReference
    execution_id: ExecutionID


class CompleteResultDescription(StrictBaseModel):
    """Inspection containing the entire retained description."""

    kind: Literal["complete"] = "complete"
    description: ResultDescription


class ResultDescriptionPreview(StrictBaseModel):
    """Fixed summary linking to the complete authenticated JSON download."""

    kind: Literal["preview"] = "preview"
    result_ref: ResultReference
    execution_id: ExecutionID
    omitted: tuple[Literal["description"]] = ("description",)
    download_url: str


class TableDownload(StrictBaseModel):
    """Authenticated Arrow IPC stream address for one validated retained output."""

    result_ref: ResultReference
    output_name: str
    download_url: str


ResultDescriptionInspection = Annotated[
    CompleteResultDescription | ResultDescriptionPreview, Field(discriminator="kind")
]


class ResultRowPage(StrictBaseModel):
    """Complete JSON rows and an opaque continuation; null means completion."""

    rows: list[dict[str, str | float | bool | None]]
    next_cursor: str | None
