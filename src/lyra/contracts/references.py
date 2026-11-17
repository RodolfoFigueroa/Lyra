"""Strict durable addresses, separate from job and report-local identifiers."""

import re
from typing import Annotated

from pydantic import BeforeValidator, Field, TypeAdapter

from lyra.contracts.errors import InvalidExecutionIDError, InvalidResultReferenceError


def _validate_reference(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        msg = "Expected 32 lowercase hexadecimal characters"
        raise ValueError(msg)
    return value


ResultReference = Annotated[str, BeforeValidator(_validate_reference)]
ExecutionID = Annotated[str, BeforeValidator(_validate_reference)]
RetrievalName = Annotated[str, Field(strict=True)]
_RETRIEVAL_NAME = TypeAdapter(RetrievalName)


def validate_retrieval_name(value: object) -> str:
    """Validate a string name; membership is resolved from retained declarations.

    Returns:
        The exact string without coercion or normalization.
    """
    return _RETRIEVAL_NAME.validate_python(value)


def validate_result_reference(
    value: object, output_name: str | None = None, *, input_name: str | None = None
) -> str:
    """Return a validated reference, preserving lookup context on failure.

    Raises:
        InvalidResultReferenceError: If the reference is malformed.
    """
    try:
        return _validate_reference(value)
    except ValueError as error:
        raise InvalidResultReferenceError(
            value, output_name, input_name=input_name
        ) from error


def validate_execution_id(value: object) -> str:
    """Return a validated execution ID.

    Raises:
        InvalidExecutionIDError: If the ID is malformed.
    """
    try:
        return _validate_reference(value)
    except ValueError as error:
        raise InvalidExecutionIDError(value) from error
