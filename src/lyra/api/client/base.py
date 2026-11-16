"""Shared request construction and response handling for API clients."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from lyra.api.exceptions import ServiceUnavailableError


def parse_retry_after(value: str | None) -> float | None:
    """Parse finite nonnegative seconds or an HTTP date.

    Returns:
        Valid delay guidance, otherwise None.
    """
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
        seconds = max(0.0, seconds)
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def service_unavailable_error(
    payload: object,
    retry_after: str | None,
) -> ServiceUnavailableError | None:
    """Decode a structured service-unavailable response when present.

    Returns:
        A typed service error, or ``None`` for an unrecognized payload.
    """
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    message = detail.get("message")
    retryable = detail.get("retryable")
    if not isinstance(code, str) or not isinstance(message, str):
        return None
    if not isinstance(retryable, bool):
        return None
    retry_after_seconds = parse_retry_after(retry_after)
    return ServiceUnavailableError(
        message,
        code=code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )


class TransportConfig:
    """Base class for Lyra API clients, containing shared logic and configuration."""

    def __init__(
        self,
        host: str,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Initialize shared client configuration.

        Args:
            host: The API server hostname.
            headers: Default HTTP headers to include in HTTP requests. If None,
                defaults to an empty dict.
            api_key: Bearer token used by this transport's protected routes.
            secure: Whether to use HTTPS. Defaults to True.

        """
        self.host = host.rstrip("/")
        self.headers = dict(headers or {})
        self._auth_headers = dict(self.headers)
        if api_key is not None:
            self._auth_headers["Authorization"] = f"Bearer {api_key}"
        self.secure = secure

    def _http_url(self, path: str) -> str:
        if path.lower().startswith(("http://", "https://")):
            return path
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.host}/{path.lstrip('/')}"
