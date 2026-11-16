"""HTTP adapter for the shared retained-resource domain errors."""

from starlette.responses import JSONResponse

from lyra.contracts.errors import ResultAccessError
from lyra.contracts.retrieval_errors import error_payload, error_status
from lyra.server.response_budget import ResponseBudget


def retrieval_error_response(error: ResultAccessError) -> JSONResponse:
    """Return a bounded structured error preserving requested-resource context."""
    payload = error_payload(error)
    response = JSONResponse(
        {"detail": payload.model_dump(mode="json")}, status_code=error_status(payload)
    )
    return ResponseBudget[JSONResponse](lambda value: len(value.body)).require_fits(
        response
    )
