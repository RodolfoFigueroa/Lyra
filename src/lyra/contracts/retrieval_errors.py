"""Shared retained-lookup payloads and reversible domain error mappings."""

from typing import Literal

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from lyra.contracts import errors
from lyra.contracts.strict import StrictBaseModel
from lyra.contracts.types import JsonValue

_RESULT_ERRORS = {
    "invalid_result_reference": (
        errors.InvalidResultReferenceError,
        422,
        "Malformed result reference.",
    ),
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
    "invalid_execution_id": (
        errors.InvalidExecutionIDError,
        422,
        "Malformed execution ID.",
    ),
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

    code: Literal[
        "invalid_result_reference", "result_not_found", "result_integrity_error"
    ]
    message: str
    result_ref: JsonValue
    output_name: str | None
    input_name: str | None = Field(default=None, exclude_if=lambda value: value is None)


class LocationInputUnavailableErrorPayload(StrictBaseModel):
    """An input whose published availability is not materialized."""

    model_config = ConfigDict(strict=True)

    code: Literal["location_input_unavailable"]
    message: str
    result_ref: JsonValue
    input_name: str


class ReportErrorPayload(StrictBaseModel):
    """A report failure scoped to the requested execution."""

    model_config = ConfigDict(strict=True)

    code: Literal["invalid_execution_id", "report_not_found", "report_integrity_error"]
    message: str
    execution_id: JsonValue


class PaginationErrorPayload(StrictBaseModel):
    """Invalid pagination scoped to the requested result, output and parameter."""

    model_config = ConfigDict(strict=True)

    code: Literal["invalid_pagination"]
    message: str
    result_ref: JsonValue
    output_name: str | None
    input_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    parameter: Literal["cursor", "limit"]


class RowTooLargeErrorPayload(StrictBaseModel):
    """Bounded oversized-row failure with a complete-table download link."""

    model_config = ConfigDict(strict=True)

    code: Literal["row_too_large"]
    message: str
    result_ref: JsonValue
    output_name: str | None
    input_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    download_url: str


RetrievalErrorPayload = (
    ResultErrorPayload
    | ReportErrorPayload
    | PaginationErrorPayload
    | RowTooLargeErrorPayload
    | LocationInputUnavailableErrorPayload
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
                "result_ref": error.result_ref,
                "input_name": error.input_name,
            }
        )
    if isinstance(error, errors.RowTooLargeError):
        return RowTooLargeErrorPayload.model_validate(
            {
                "code": "row_too_large",
                "message": "The next row exceeds the JSON page size limit.",
                "result_ref": error.result_ref,
                "output_name": error.output_name,
                "input_name": error.input_name,
                "download_url": error.download_url,
            }
        )
    if isinstance(error, errors.InvalidPaginationError):
        return PaginationErrorPayload.model_validate(
            {
                "code": "invalid_pagination",
                "message": "Invalid pagination for this output.",
                "result_ref": error.result_ref,
                "output_name": error.output_name,
                "input_name": error.input_name,
                "parameter": error.parameter,
            }
        )
    for code, (error_type, _, message) in _RESULT_ERRORS.items():
        if isinstance(error, error_type):
            return ResultErrorPayload.model_validate(
                {
                    "code": code,
                    "message": message,
                    "result_ref": error.result_ref,
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
                    "execution_id": error.execution_id,
                }
            )
    msg = f"Unmapped retrieval error: {type(error).__name__}"
    raise TypeError(msg)


def error_status(payload: RetrievalErrorPayload) -> int:
    """Return the HTTP status associated with a validated domain payload."""
    if isinstance(payload, LocationInputUnavailableErrorPayload):
        return 409
    if isinstance(payload, PaginationErrorPayload | RowTooLargeErrorPayload):
        return 422
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
        return errors.LocationInputUnavailableError(
            parsed.result_ref, parsed.input_name
        )
    if isinstance(parsed, RowTooLargeErrorPayload):
        return errors.RowTooLargeError(
            parsed.result_ref,
            parsed.output_name,
            parsed.download_url,
            input_name=parsed.input_name,
        )
    if isinstance(parsed, PaginationErrorPayload):
        return errors.InvalidPaginationError(
            parsed.result_ref,
            parsed.output_name,
            parsed.parameter,
            input_name=parsed.input_name,
        )
    if isinstance(parsed, ResultErrorPayload):
        return _RESULT_ERRORS[parsed.code][0](
            parsed.result_ref, parsed.output_name, input_name=parsed.input_name
        )
    return _REPORT_ERRORS[parsed.code][0](parsed.execution_id)
