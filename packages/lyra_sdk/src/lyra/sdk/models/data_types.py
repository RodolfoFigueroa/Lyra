"""Public ID-selection schema metadata."""

from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.types import JsonObject
from pydantic import Field


class DataTypesResponse(StrictBaseModel):
    """Schema accepted by every named location input."""

    location_selection_schema: JsonObject = Field(
        description="Select opaque IDs by collection and unit type.",
    )
