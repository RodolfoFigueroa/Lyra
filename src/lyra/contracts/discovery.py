"""Serializable family discovery responses."""

from lyra.contracts.locations import LocationContract, LocationFamily
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class EditionChoice(StrictBaseModel):
    """Public edition and its input-specific accepted selection types."""

    edition: str | None
    accepted_selections: dict[str, list[tuple[str, str]]]


class FamilySummary(StrictBaseModel):
    """Compact discovery entry without schemas or execution graphs."""

    name: str
    description: str
    output_granularity: dict[str, str | None]
    requires_edition: bool
    editions: list[EditionChoice]


class MetricInspection(StrictBaseModel):
    """Shared family contract, optionally bound to a concrete public edition."""

    family: FamilySummary
    edition: str | None
    node: str | None
    edition_metadata: JsonObject
    native_inputs: dict[str, LocationFamily]
    parameters: JsonObject
    outputs: dict[str, OutputMetadata]
    bindings: dict[str, LocationContract]
    accepted_selections: dict[str, list[LocationContract]]
