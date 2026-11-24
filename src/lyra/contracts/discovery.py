"""Serializable metric discovery responses."""

from lyra.contracts.origins import Origin
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.providers import ProviderQuerySchema
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class MetricSummary(StrictBaseModel):
    """Compact public metric description."""

    name: str
    description: str
    output_granularity: dict[str, str | None]
    accepted_selections: dict[str, list[str]]


class ProviderDescription(StrictBaseModel):
    """A registered dataset and its selection capabilities in the active catalog."""

    scope: ProviderScope
    resource: str
    record_schema: OutputMetadata
    crs: str
    accepted_sources: list[str]
    description: str | None
    cache: bool
    query_schema: ProviderQuerySchema | None
    query_capable: bool
    membership_capable: bool


class LocationInput(StrictBaseModel):
    """A caller-supplied input resolved to its declared provider."""

    target_provider: str


class OperationInput(LocationInput):
    """The immediate source and correspondence schema of an explicit operation."""

    operation: str
    source_provider: str
    record_schema: OutputMetadata


class MetricInspection(StrictBaseModel):
    """Concrete public metric inputs, parameters, and outputs."""

    metric: MetricSummary
    node: str
    location_inputs: dict[str, OperationInput | LocationInput]
    parameters: JsonObject
    outputs: dict[str, OutputMetadata]
    computed_inputs: dict[str, JsonObject]
    providers: dict[str, ProviderDescription]
    origins: list[Origin]
