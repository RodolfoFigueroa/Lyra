"""Shared deadlines, progress comparison, and retry policy for job polling."""

from __future__ import annotations

import math
import random
import time
from typing import TYPE_CHECKING, cast

from lyra.api.client import endpoints
from lyra.api.client.results import successful_status
from lyra.api.exceptions import (
    DownloadError,
    JobNotReadyError,
    JobPollingError,
    JobWaitTimeoutError,
    ServiceUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from lyra.api.client.endpoints import RequestSpec
    from lyra.contracts.jobs import (
        JobProgress,
        JobStatusInfo,
    )


class PollingState:
    """Track one wait without changing the transport or remote job."""

    def __init__(
        self, job_id: str, timeout: float | None, poll_interval: float
    ) -> None:
        """Validate wait options and start a monotonic deadline.

        Raises:
            ValueError: If timing arguments are not finite or out of range.
        """
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            msg = "poll_interval must be positive and finite"
            raise ValueError(msg)
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            msg = "timeout must be nonnegative and finite, or None"
            raise ValueError(msg)
        self.job_id = job_id
        self.deadline = None if timeout is None else time.monotonic() + timeout
        self.poll_interval = poll_interval
        self.failures = 0
        self.progress: dict[str, object] | None = None

    def remaining(self) -> float | None:
        """Return remaining time, raising when the wait has expired.

        Raises:
            JobWaitTimeoutError: If the caller's deadline has elapsed.
        """
        if self.deadline is None:
            return None
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            msg = "Job wait timed out."
            raise JobWaitTimeoutError(msg, job_id=self.job_id)
        return remaining

    def request_timeout(self, default: float) -> float:
        """Return a request timeout bounded by the remaining time."""
        remaining = self.remaining()
        return default if remaining is None else min(default, remaining)

    def delay(self, seconds: float | None = None) -> float:
        """Return a jittered polling delay or bounded explicit retry delay."""
        delay = (
            self.poll_interval * random.SystemRandom().uniform(0.9, 1.1)
            if seconds is None
            else seconds
        )
        remaining = self.remaining()
        return delay if remaining is None else min(delay, remaining)

    def changed(self, progress: JobProgress | None) -> bool:
        """Return whether snapshot content changed, ignoring timestamps."""
        if progress is None:
            return False
        content = progress.model_dump(exclude={"timestamp"})
        if content == self.progress:
            return False
        self.progress = content
        return True

    def retry_delay(self, error: DownloadError | ServiceUnavailableError) -> float:
        """Choose bounded retry delay or propagate a permanent failure.

        Returns:
            The delay before the next observation.

        Raises:
            JobPollingError: After five consecutive retries have been exhausted.
        """
        self.remaining()
        if not error.retryable:
            raise error
        if self.failures >= 5:
            msg = "Job polling retries exhausted."
            raise JobPollingError(
                msg, job_id=self.job_id, attempts=self.failures
            ) from error
        delay = min(30.0, 2.0**self.failures * random.SystemRandom().uniform(0.9, 1.1))
        self.failures += 1
        if error.retry_after_seconds is not None:
            delay = error.retry_after_seconds
        return self.delay(delay)

    def observations(
        self,
    ) -> Generator[
        RequestSpec[JobStatusInfo] | JobProgress | float,
        JobStatusInfo | None,
        JobStatusInfo,
    ]:
        """Drive polling policy through observations, callbacks, and delays.

        Returns:
            The successful terminal result.

        Yields:
            Requests, progress snapshots, and delays for the mode-specific driver.
        """
        while True:
            self.remaining()
            try:
                response = yield endpoints.get_job(self.job_id)
                self.remaining()
            except JobNotReadyError as exc:
                self.remaining()
                response = exc.snapshot
            except (DownloadError, ServiceUnavailableError) as exc:
                yield self.retry_delay(exc)
                continue
            self.failures = 0
            snapshot = cast("JobStatusInfo", response)
            if self.changed(snapshot.progress):
                yield cast("JobProgress", snapshot.progress)
            terminal = snapshot.status in {"succeeded", "failed"}
            if terminal:
                return successful_status(snapshot)  # ruff: ignore[return-in-generator]
            yield self.delay()


def advance_polling(
    policy: Generator[
        RequestSpec[JobStatusInfo] | JobProgress | float,
        JobStatusInfo | None,
        JobStatusInfo,
    ],
    response: JobStatusInfo | None = None,
    *,
    error: DownloadError | ServiceUnavailableError | JobNotReadyError | None = None,
) -> RequestSpec[JobStatusInfo] | JobProgress | float | JobStatusInfo:
    """Advance policy without intercepting exceptions from user callbacks.

    Returns:
        The next action or the completed result.
    """
    try:
        return policy.send(response) if error is None else policy.throw(error)
    except StopIteration as completed:
        return cast("JobStatusInfo", completed.value)
