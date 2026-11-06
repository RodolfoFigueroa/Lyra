"""HTTP metadata for named ID selections."""

from fastapi import APIRouter

from lyra.contracts.data_types import DataTypesResponse
from lyra.contracts.locations import LocationSelection

router = APIRouter(tags=["Catalog"])


@router.get("/data-types")
async def list_data_types() -> DataTypesResponse:
    """Return the ID-only schema accepted by named location inputs."""
    return DataTypesResponse(
        location_selection_schema=LocationSelection.model_json_schema()
    )
