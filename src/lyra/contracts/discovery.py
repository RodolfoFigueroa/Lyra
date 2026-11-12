"""Serializable metric discovery responses."""

from lyra.contracts.locations import LocationContract, LocationFamily
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class MetricSummary(StrictBaseModel):
    """Compact public metric description."""

    name: str
    description: str
    output_granularity: dict[str, str | None]
    accepted_selections: dict[str, list[tuple[str, str]]]


class MetricInspection(StrictBaseModel):
    """Concrete public metric inputs, parameters, and outputs."""

    metric: MetricSummary
    node: str
    native_inputs: dict[str, LocationFamily]
    parameters: JsonObject
    outputs: dict[str, OutputMetadata]
    bindings: dict[str, LocationContract]
    accepted_selections: dict[str, list[LocationContract]]
    computed_inputs: dict[str, JsonObject]
