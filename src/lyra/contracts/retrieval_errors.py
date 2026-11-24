"""Shared retained-lookup payloads and reversible domain error mappings."""

from typing import Literal

from pydantic import ConfigDict, TypeAdapter, ValidationError

from lyra.contracts import errors
from lyra.contracts.references import JobId
from lyra.contracts.strict import StrictBaseModel

_RESULT_ERRORS = {
    "result_not_found": (
        errors.ResultNotFoundError,
        404,
        "Result or output not found.",
    ),
    "result_integrity_error": (
        errors.ResultIntegrityError,
        500,
        "Required retained result data is damaged.",
    ),
}
_REPORT_ERRORS = {
    "report_not_found": (
        errors.ReportNotFoundError,
        404,
        "Execution report not found.",
    ),
    "report_integrity_error": (
        errors.ReportIntegrityError,
        500,
        "Required retained report data is damaged.",
    ),
}


class ResultErrorPayload(StrictBaseModel):
    """A result failure, including explicit null output context for whole reads."""

    model_config = ConfigDict(strict=True)

    code: Literal["result_not_found", "result_integrity_error"]
    message: str
    job_id: JobId
    output_name: str | None
    input_name: str | None


class LocationInputUnavailableErrorPayload(StrictBaseModel):
    """An input whose published availability is not materialized."""

    model_config = ConfigDict(strict=True)

    code: Literal["location_input_unavailable"]
    message: str
    job_id: JobId
    input_name: str


class ReportErrorPayload(StrictBaseModel):
    """A report failure scoped to the requested execution."""

    model_config = ConfigDict(strict=True)

    code: Literal["report_not_found", "report_integrity_error"]
    message: str
    job_id: JobId


RetrievalErrorPayload = (
    ResultErrorPayload | ReportErrorPayload | LocationInputUnavailableErrorPayload
)
_PAYLOAD = TypeAdapter(RetrievalErrorPayload)


def error_payload(error: errors.ResultAccessError) -> RetrievalErrorPayload:
    """Describe a lookup failure without exposing its cause or diagnostic text.

    Returns:
        The shared payload with all resource attributes intact.

    Raises:
        TypeError: If the exception has no registered transport mapping.
    """
    if isinstance(error, errors.LocationInputUnavailableError):
        return LocationInputUnavailableErrorPayload.model_validate(
            {
                "code": "location_input_unavailable",
                "message": "The location input was not materialized for this result.",
                "job_id": error.job_id,
                "input_name": error.input_name,
            }
        )
    for code, (error_type, _, message) in _RESULT_ERRORS.items():
        if isinstance(error, error_type):
            return ResultErrorPayload.model_validate(
                {
                    "code": code,
                    "message": message,
                    "job_id": error.job_id,
                    "output_name": error.output_name,
                    "input_name": error.input_name,
                }
            )
    for code, (error_type, _, message) in _REPORT_ERRORS.items():
        if isinstance(error, error_type):
            return ReportErrorPayload.model_validate(
                {
                    "code": code,
                    "message": message,
                    "job_id": error.job_id,
                }
            )
    msg = f"Unmapped retrieval error: {type(error).__name__}"
    raise TypeError(msg)


def error_status(payload: RetrievalErrorPayload) -> int:
    """Return the HTTP status associated with a validated domain payload."""
    if isinstance(payload, LocationInputUnavailableErrorPayload):
        return 409
    if isinstance(payload, ResultErrorPayload):
        return _RESULT_ERRORS[payload.code][1]
    return _REPORT_ERRORS[payload.code][1]


def decode_error(payload: object, *, status: int) -> errors.ResultAccessError | None:
    """Decode exact codes and required fields, never human message text.

    Returns:
        A domain exception, or None for malformed/unrecognized/status-mismatched data.
    """
    try:
        parsed = _PAYLOAD.validate_python(payload)
    except ValidationError:
        return None
    if error_status(parsed) != status:
        return None
    return _decode_validated_error(parsed)


def _decode_validated_error(parsed: RetrievalErrorPayload) -> errors.ResultAccessError:
    if isinstance(parsed, LocationInputUnavailableErrorPayload):
        return errors.LocationInputUnavailableError(parsed.job_id, parsed.input_name)
    if isinstance(parsed, ResultErrorPayload):
        return _RESULT_ERRORS[parsed.code][0](
            parsed.job_id, parsed.output_name, input_name=parsed.input_name
        )
    return _REPORT_ERRORS[parsed.code][0](parsed.job_id)
