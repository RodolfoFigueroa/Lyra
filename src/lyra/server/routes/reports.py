"""Authenticated execution-report access independent of job lifetimes."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from lyra.contracts.errors import ResultAccessError
from lyra.contracts.reports import ExecutionReport, ExecutionReportInspection
from lyra.server.agent_auth import require_agent_key
from lyra.server.response_budget import http_json_response, http_response_budget
from lyra.server.results import inspect_report
from lyra.server.routes.retrieval_errors import retrieval_error_response
from lyra.server.runtime import ConfigDependency, ResultServiceDependency

router = APIRouter(tags=["Reports"], dependencies=[Depends(require_agent_key)])


@router.get(
    "/executions/{execution_id}/report/inspection",
    response_model=ExecutionReportInspection,
)
async def inspect_execution_report(
    execution_id: str, results: ResultServiceDependency, config: ConfigDependency
) -> JSONResponse:
    """Return a complete report or a bounded authenticated download link."""
    try:
        report = await results.report(execution_id)
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(
        inspect_report(
            report,
            public_api_base_url=config.api.public_base_url,
            budget=http_response_budget(),
        )
    )


@router.get(
    "/executions/{execution_id}/report/download", response_model=ExecutionReport
)
async def download_report(
    execution_id: str, results: ResultServiceDependency
) -> JSONResponse:
    """Return complete report JSON from retained metadata without a size limit."""
    try:
        report = await results.report(execution_id)
    except ResultAccessError as error:
        return retrieval_error_response(error)
    return http_json_response(report)
