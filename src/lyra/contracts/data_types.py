"""Public ID-selection schema metadata."""

from pydantic import Field

from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonObject


class DataTypesResponse(StrictBaseModel):
    """Schema accepted by every named location input."""

    location_selection_schema: JsonObject = Field(
        description="Select opaque IDs by collection and unit type.",
    )
