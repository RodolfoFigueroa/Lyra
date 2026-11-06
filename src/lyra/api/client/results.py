"""Result and JSON download policies shared by both clients."""

from lyra.api.client.endpoints import check_status, raise_pending
from lyra.api.exceptions import MetricRunError
from lyra.contracts.jobs import (
    FailedJobResult,
    JobStatusInfo,
    TableJobResult,
    TerminalJobResult,
)


def successful_result(result: TerminalJobResult) -> TableJobResult:
    """Return successful named outputs or raise the job's failure.

    Raises:
        MetricRunError: If execution failed.
    """
    if isinstance(result, FailedJobResult):
        raise MetricRunError(result.job_id, result.error)
    return result


def successful_status(snapshot: JobStatusInfo) -> JobStatusInfo:
    """Return successful completion metadata, raising for failed execution.

    Raises:
        MetricRunError: If the job failed.
    """
    if snapshot.status == "failed":
        raise MetricRunError(snapshot.job_id, snapshot.error or {})
    return snapshot


def download_needs_text(status: int) -> bool:
    """Return whether an HTTP error needs decoding before writing a file."""
    return status != 200


def validate_download(
    status: int, text: str, retry_after: str | None, *, operation: str
) -> None:
    """Validate HTTP status before opening the destination."""
    if status == 202:
        raise_pending(text, operation=operation)
    check_status(status, text, retry_after, operation=operation)
