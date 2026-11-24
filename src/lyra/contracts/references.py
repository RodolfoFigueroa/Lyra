"""Validated job identifiers and exact retained table names."""

from typing import Annotated
from uuid import uuid4

from pydantic import Field, StringConstraints, TypeAdapter

JobId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=False, pattern=r"^[0-9a-f]{32}$"),
]
_JOB_ID = TypeAdapter(JobId)


def new_job_id() -> str:
    """Return a fresh identifier for one submission or local run."""
    return uuid4().hex


def validate_job_id(value: object) -> str:
    """Return an exact lowercase hexadecimal job identifier."""
    return _JOB_ID.validate_python(value)


RetrievalName = Annotated[str, Field(strict=True)]
_RETRIEVAL_NAME = TypeAdapter(RetrievalName)


def validate_retrieval_name(value: object) -> str:
    """Validate a string name; membership is resolved from retained declarations.

    Returns:
        The exact string without coercion or normalization.
    """
    return _RETRIEVAL_NAME.validate_python(value)
