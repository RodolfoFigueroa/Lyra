"""Authenticated retained descriptions independent of job lifetimes."""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from lyra.contracts.errors import ResultAccessError
from lyra.contracts.references import JobId
from lyra.contracts.results import ResultDescription
from lyra.server.agent_auth import require_agent_key
from lyra.server.response_budget import http_json_response
from lyra.server.routes.retrieval_errors import retrieval_error_response
from lyra.server.runtime import ResultServiceDependency
from lyra.server.table_response import TableResponse

router = APIRouter(tags=["Results"], dependencies=[Depends(require_agent_key)])


@router.get(
    "/results/{job_id}/tables/download",
    response_class=FileResponse,
    responses={200: {"content": {"application/vnd.apache.arrow.stream": {}}}},
)
async def download_table(
    job_id: JobId, output_name: str, results: ResultServiceDependency
) -> TableResponse:
    """Return one retained output as a generated Arrow IPC streaming response."""
    return TableResponse(results.stream_table(job_id, output_name))


@router.get("/results/{job_id}", response_model=ResultDescription)
async def describe_result(
    job_id: JobId, results: ResultServiceDependency
) -> JSONResponse:
    """Return generated complete description JSON without an inspection size limit."""
    try:
        description = await results.describe(job_id)
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(description)


@router.get(
    "/results/{job_id}/location-inputs/download",
    response_class=FileResponse,
    responses={200: {"content": {"application/vnd.apache.arrow.stream": {}}}},
)
async def download_location_input(
    job_id: JobId, input_name: str, results: ResultServiceDependency
) -> TableResponse:
    """Return a retained location input as a generated Arrow stream."""
    return TableResponse(results.stream_location_input(job_id, input_name))
