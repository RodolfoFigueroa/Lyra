"""Result and JSON download policies shared by both clients."""

from collections.abc import Generator, KeysView
from contextlib import contextmanager

from pydantic import ValidationError

from lyra.api.client.endpoints import check_status, raise_pending
from lyra.api.exceptions import DownloadError, JobNotReadyError, MetricRunError
from lyra.contracts.errors import ResultIntegrityError
from lyra.contracts.jobs import (
    FailedJobResult,
    JobStatusInfo,
    TableJobResult,
    TerminalJobResult,
)
from lyra.contracts.results import ResultDescription


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


@contextmanager
def result_identity_errors(job_id: str) -> Generator[None]:
    """Report invalid top-level identity fields during bulk metadata reads.

    Yields:
        Control while the status and retained description are validated.

    Raises:
        ResultIntegrityError: If validation failed on a required identity.
        DownloadError: For other transport or validation failures.
    """
    try:
        yield
    except DownloadError as exc:
        cause = exc.__cause__
        if isinstance(cause, ValidationError) and any(
            error["loc"] in {("job_id",), ("metric",), ("metric_name",)}
            and error["type"] != "extra_forbidden"
            for error in cause.errors(include_input=False)
        ):
            raise ResultIntegrityError(job_id) from exc
        raise


def accepted_metric(job_id: str, snapshot: JobStatusInfo) -> str:
    """Return the accepted metric for a successful bulk read.

    Raises:
        JobNotReadyError: If the job is still queued or running.
        ResultIntegrityError: If the successful status lacks matching identity.
    """
    if snapshot.status in {"queued", "running"}:
        raise JobNotReadyError(snapshot)
    successful_status(snapshot)
    if snapshot.job_id != job_id or snapshot.metric is None:
        raise ResultIntegrityError(job_id)
    return snapshot.metric


def verified_output_names(
    job_id: str, metric: str, description: ResultDescription
) -> KeysView[str]:
    """Return the names in the description belonging to this job.

    Raises:
        ResultIntegrityError: If the retained job or metric differs.
    """
    if description.job_id != job_id or description.metric_name != metric:
        raise ResultIntegrityError(job_id)
    return description.outputs.keys()


def download_needs_text(status: int) -> bool:
    """Return whether an HTTP error needs decoding before writing a file."""
    return status != 200


def validate_download(
    status: int,
    text: str,
    retry_after: str | None,
    *,
    operation: str,
    pending_result: bool = True,
) -> None:
    """Validate HTTP status before opening the destination."""
    if status == 202 and pending_result:
        raise_pending(text, operation=operation)
    check_status(status, text, retry_after, operation=operation)
