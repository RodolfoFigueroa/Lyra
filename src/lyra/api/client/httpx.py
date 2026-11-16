"""Native HTTPX JSON and streaming transports sharing endpoint contracts."""

from __future__ import annotations

import ssl
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Self, TypeVar

import httpx

from lyra.api.client.base import TransportConfig
from lyra.api.client.endpoints import validate_content_type, validate_response
from lyra.api.client.results import download_needs_text, validate_download
from lyra.api.exceptions import DownloadError

if TYPE_CHECKING:
    import os
    from collections.abc import Generator
    from types import TracebackType
    from typing import IO, BinaryIO

    from lyra.api.client.endpoints import DownloadSpec, RequestSpec

_ResponseT = TypeVar("_ResponseT")


def _open_destination(path: str | os.PathLike[str]) -> BinaryIO:
    """Open a synchronous sink owned by the download's context.

    Local writes and closure stay in the calling thread, so cancellation cannot
    leave an executor write running against a closing file. Slow filesystems can
    block that thread; network reads remain incremental and asynchronous.

    Returns:
        A binary file to be closed by the caller.
    """
    return Path(path).open("wb")


class _DownloadDestination:
    """Commit an atomic destination only after file and response contexts exit."""

    def __init__(self, path: str | os.PathLike[str], *, atomic: bool) -> None:
        self.path = Path(path)
        self.atomic = atomic
        self.temporary: Path | None = None

    @contextmanager
    def open(self) -> Generator[IO[bytes]]:
        """Open the sink after response validation, closing it in the caller.

        Yields:
            The binary sink for incremental response writes.
        """
        if self.atomic:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, delete=False
            ) as file:
                self.temporary = Path(file.name)
                yield file.file
        else:
            with _open_destination(self.path) as file:
                yield file

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.temporary is not None:
            try:
                if exc_type is None:
                    self.temporary.replace(self.path)
            finally:
                self.temporary.unlink(missing_ok=True)


def transport_error(
    operation: str, exc: Exception, *, submission: bool = False
) -> DownloadError:
    """Return polling guidance and submission context for an HTTPX failure."""
    retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLError):
            retryable = False
        cause = cause.__cause__ or cause.__context__
    message = f"Failed to {operation}: request error: {exc}"
    if submission:
        message += (
            " The job may have been accepted; another submission may duplicate it."
        )
    return DownloadError(message, retryable=retryable)


def _validate(spec: RequestSpec[_ResponseT], response: httpx.Response) -> _ResponseT:
    return validate_response(
        spec,
        response.status_code,
        response.text,
        response.headers.get("content-type", ""),
        response.headers.get("Retry-After"),
    )


class SyncHTTPTransport(TransportConfig):
    """Own a reusable synchronous HTTPX client."""

    def __init__(
        self,
        host: str,
        timeout: float | httpx.Timeout | None = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Configure native HTTP timeouts and endpoint credentials."""
        super().__init__(host, headers, api_key=api_key, secure=secure)
        self._client = httpx.Client(timeout=timeout)

    def check_open(self) -> None:
        """Reject operations after closure.

        Raises:
            RuntimeError: If the client is closed.
        """
        if self._client.is_closed:
            msg = "Client is closed."
            raise RuntimeError(msg)

    def request(self, spec: RequestSpec[_ResponseT]) -> _ResponseT:
        """Return the validated response to one request without retries."""
        self.check_open()
        try:
            response = self._client.request(
                spec.method,
                self._http_url(spec.path),
                params=spec.params,
                json=spec.json_body,
                headers=self._auth_headers if spec.authenticated else self.headers,
            )
            return _validate(spec, response)
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(
                spec.operation,
                exc,
                submission=spec.method == "POST" and spec.path == "jobs",
            )
            raise error from exc

    def read_bytes(self, spec: DownloadSpec) -> bytes:
        """Read a complete validated binary response, closing it before return.

        Returns:
            The complete body of a successful response with the expected type.

        """
        self.check_open()
        try:
            with self._client.stream(
                "GET", self._http_url(spec.path), headers=self._auth_headers
            ) as response:
                if download_needs_text(response.status_code):
                    response.read()
                    validate_download(
                        response.status_code,
                        response.text,
                        response.headers.get("Retry-After"),
                        operation=spec.operation,
                        pending_result=False,
                    )
                validate_content_type(
                    response.headers.get("content-type", ""),
                    spec.content_type,
                    spec.operation,
                )
                return response.read()
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(spec.operation, exc)
            raise error from exc

    def download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
        atomic: bool = False,
        content_type: str | None = None,
    ) -> None:
        """Stream bytes, optionally replacing a durable destination atomically.

        Atomic downloads accept only HTTP 200; job downloads additionally decode
        pending status snapshots. All errors are validated before opening a file.
        """
        self.check_open()
        try:
            self._download(
                route, path, operation, atomic=atomic, content_type=content_type
            )
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(operation, exc)
            raise error from exc

    def _download(
        self,
        route: str,
        path: str | os.PathLike[str],
        operation: str,
        *,
        atomic: bool,
        content_type: str | None,
    ) -> None:
        with (
            _DownloadDestination(path, atomic=atomic) as destination,
            self._client.stream(
                "GET", self._http_url(route), headers=self._auth_headers
            ) as response,
        ):
            if download_needs_text(response.status_code):
                response.read()
                validate_download(
                    response.status_code,
                    response.text,
                    response.headers.get("Retry-After"),
                    operation=operation,
                    pending_result=not atomic,
                )
            validate_content_type(
                response.headers.get("content-type", ""), content_type, operation
            )
            with destination.open() as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)

    def close(self) -> None:
        """Close the connection pool."""
        self._client.close()

    def __enter__(self) -> Self:
        """Return the open transport."""
        self.check_open()
        return self

    def __exit__(self, *args: object) -> None:
        """Close the connection pool on context exit."""
        self.close()


class AsyncHTTPTransport(TransportConfig):
    """Own an HTTPX async client, executing operations in the caller's task."""

    def __init__(
        self,
        host: str,
        timeout: float | httpx.Timeout | None = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Configure native HTTP timeouts and endpoint credentials."""
        super().__init__(host, headers, api_key=api_key, secure=secure)
        self._client = httpx.AsyncClient(timeout=timeout)

    def check_open(self) -> None:
        """Reject operations after closure.

        Raises:
            RuntimeError: If the client is closed.
        """
        if self._client.is_closed:
            msg = "Client is closed."
            raise RuntimeError(msg)

    async def request(self, spec: RequestSpec[_ResponseT]) -> _ResponseT:
        """Return the validated response to one request in the caller's task."""
        self.check_open()
        try:
            response = await self._client.request(
                spec.method,
                self._http_url(spec.path),
                params=spec.params,
                json=spec.json_body,
                headers=self._auth_headers if spec.authenticated else self.headers,
            )
            return _validate(spec, response)
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(
                spec.operation,
                exc,
                submission=spec.method == "POST" and spec.path == "jobs",
            )
            raise error from exc

    async def read_bytes(self, spec: DownloadSpec) -> bytes:
        """Read a complete validated binary response, closing it before return.

        Returns:
            The complete body of a successful response with the expected type.

        """
        self.check_open()
        try:
            async with self._client.stream(
                "GET", self._http_url(spec.path), headers=self._auth_headers
            ) as response:
                if download_needs_text(response.status_code):
                    await response.aread()
                    validate_download(
                        response.status_code,
                        response.text,
                        response.headers.get("Retry-After"),
                        operation=spec.operation,
                        pending_result=False,
                    )
                validate_content_type(
                    response.headers.get("content-type", ""),
                    spec.content_type,
                    spec.operation,
                )
                return await response.aread()
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(spec.operation, exc)
            raise error from exc

    async def download(
        self,
        route: str,
        path: str | os.PathLike[str],
        *,
        operation: str,
        atomic: bool = False,
        content_type: str | None = None,
    ) -> None:
        """Stream bytes with the same destination and status policies as sync."""
        self.check_open()
        try:
            await self._download(
                route, path, operation, atomic=atomic, content_type=content_type
            )
        except (httpx.RequestError, httpx.InvalidURL) as exc:
            error = transport_error(operation, exc)
            raise error from exc

    async def _download(
        self,
        route: str,
        path: str | os.PathLike[str],
        operation: str,
        *,
        atomic: bool,
        content_type: str | None,
    ) -> None:
        with _DownloadDestination(path, atomic=atomic) as destination:
            async with self._client.stream(
                "GET", self._http_url(route), headers=self._auth_headers
            ) as response:
                if download_needs_text(response.status_code):
                    await response.aread()
                    validate_download(
                        response.status_code,
                        response.text,
                        response.headers.get("Retry-After"),
                        operation=operation,
                        pending_result=not atomic,
                    )
                validate_content_type(
                    response.headers.get("content-type", ""), content_type, operation
                )
                with destination.open() as file:
                    async for chunk in response.aiter_bytes():
                        file.write(chunk)

    async def aclose(self) -> None:
        """Close the connection pool in the caller's task."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Return the open transport."""
        self.check_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Await connection pool closure on context exit."""
        await self.aclose()
