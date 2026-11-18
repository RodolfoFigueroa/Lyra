"""Authenticated retained descriptions independent of job lifetimes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from lyra.contracts.errors import InvalidPaginationError, ResultAccessError
from lyra.contracts.results import (
    ResultDescription,
    ResultDescriptionInspection,
    ResultRowPage,
)
from lyra.server.agent_auth import require_agent_key
from lyra.server.response_budget import http_json_response, http_response_budget
from lyra.server.result_rows import RowPageContext
from lyra.server.results import inspect_description
from lyra.server.routes.retrieval_errors import retrieval_error_response
from lyra.server.runtime import ConfigDependency, ResultServiceDependency
from lyra.server.table_response import TableResponse

router = APIRouter(tags=["Results"], dependencies=[Depends(require_agent_key)])


@router.get(
    "/results/{result_ref}/tables/{output_name}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/vnd.apache.arrow.stream": {}}}},
)
async def download_table(
    result_ref: str, output_name: str, results: ResultServiceDependency
) -> TableResponse:
    """Return one retained output as a generated Arrow IPC streaming response."""
    return TableResponse(results.stream_table(result_ref, output_name))


@router.get("/results/{result_ref}", response_model=ResultDescriptionInspection)
async def describe_result(
    result_ref: str, results: ResultServiceDependency, config: ConfigDependency
) -> JSONResponse:
    """Return complete metadata or a bounded authenticated download link."""
    try:
        description = await results.describe(result_ref)
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(
        inspect_description(
            description,
            public_api_base_url=config.api.public_base_url,
            budget=http_response_budget(),
        )
    )


@router.get(
    "/results/{result_ref}/description/download", response_model=ResultDescription
)
async def download_description(
    result_ref: str, results: ResultServiceDependency
) -> JSONResponse:
    """Return generated complete description JSON without an inspection size limit."""
    try:
        description = await results.describe(result_ref)
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(description)


class RowQuery(BaseModel):
    """Raw query values, with domain-owned pagination validation."""

    limit: str = "100"
    cursor: str | None = None


@router.get(
    "/results/{result_ref}/tables/{output_name}/rows", response_model=ResultRowPage
)
async def result_rows(
    result_ref: str,
    output_name: str,
    results: ResultServiceDependency,
    config: ConfigDependency,
    query: Annotated[RowQuery, Query()],
) -> JSONResponse:
    """Return a SQL-selected page with shared pagination errors and byte bounds.

    Raises:
        InvalidPaginationError: Converted to the shared domain response.
    """
    try:
        try:
            parsed_limit = int(query.limit)
        except ValueError as error:
            raise InvalidPaginationError(result_ref, output_name, "limit") from error
        page = await results.rows(
            result_ref,
            output_name,
            parsed_limit,
            query.cursor,
            context=RowPageContext(config.api.public_base_url, http_response_budget()),
        )
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(page)


@router.get(
    "/results/{result_ref}/location-inputs/{input_name}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/vnd.apache.arrow.stream": {}}}},
)
async def download_location_input(
    result_ref: str, input_name: str, results: ResultServiceDependency
) -> TableResponse:
    """Return a retained location input as a generated Arrow stream."""
    return TableResponse(
        results.stream_location_input(result_ref, input_name), verify=True
    )


@router.get(
    "/results/{result_ref}/location-inputs/{input_name}/rows",
    response_model=ResultRowPage,
)
async def location_input_rows(
    result_ref: str,
    input_name: str,
    results: ResultServiceDependency,
    config: ConfigDependency,
    query: Annotated[RowQuery, Query()],
) -> JSONResponse:
    """Return bounded retained input rows with shared domain errors.

    Raises:
        InvalidPaginationError: Converted to the shared domain response.
    """
    try:
        try:
            parsed_limit = int(query.limit)
        except ValueError as error:
            raise InvalidPaginationError(
                result_ref, None, "limit", input_name=input_name
            ) from error
        page = await results.location_input_rows(
            result_ref,
            input_name,
            parsed_limit,
            query.cursor,
            context=RowPageContext(config.api.public_base_url, http_response_budget()),
        )
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(page)
