"""Shared HTTP descriptions and safe errors for retained-job lookups."""

from typing import Any

from fastapi import HTTPException

JOB_LOOKUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Job not found."},
    500: {
        "description": "A required stored job relationship is invalid.",
        "content": {
            "application/json": {
                "example": {
                    "detail": {
                        "code": "invalid_job_result",
                        "message": "Invalid stored job result.",
                        "retryable": False,
                    }
                }
            }
        },
    },
}


class InvalidJobResultHTTPError(HTTPException):
    """Safe HTTP representation of a stored job integrity failure."""

    def __init__(self) -> None:
        """Describe the failure without exposing stored data."""
        super().__init__(
            status_code=500,
            detail={
                "code": "invalid_job_result",
                "message": "Invalid stored job result.",
                "retryable": False,
            },
        )
