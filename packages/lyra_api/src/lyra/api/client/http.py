"""Client-owned asynchronous HTTP sessions and cancellable operations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

import aiofiles
import aiohttp
from lyra.api.client.base import BaseTransport
from lyra.api.client.endpoints import validate_response
from lyra.api.client.results import download_needs_text, validate_download
from lyra.api.exceptions import DownloadError

if TYPE_CHECKING:
    import os
    from collections.abc import Coroutine
    from typing import Any

    from lyra.api.client.endpoints import RequestSpec

_ResponseT = TypeVar("_ResponseT")


class HTTPTransport(BaseTransport):
    """Own a lazy session, its loop, and all outstanding request cleanup."""

    def __init__(
        self,
        host: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        *,
        api_key: str | None = None,
        secure: bool = True,
    ) -> None:
        """Capture configuration without creating a session."""
        super().__init__(host, timeout, headers, api_key=api_key, secure=secure)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: aiohttp.ClientSession | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closing: asyncio.Task[None] | None = None
        self._closed = False

    def check_open(self) -> None:
        """Check lifetime and bind first use to the caller's event loop.

        Raises:
            RuntimeError: If closed or used from another event loop.
        """
        if self._closed:
            msg = "Client is closed."
            raise RuntimeError(msg)
        self._check_loop()

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            msg = "Async client must be used and closed on its owning event loop."
            raise RuntimeError(msg)
        self._loop = loop

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        return self._session

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _perform(
        self,
        operation: Coroutine[Any, Any, _ResponseT],
        seconds: float,
    ) -> _ResponseT:
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=seconds)
            if not done:
                raise TimeoutError
            return task.result()
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def aclose(self) -> None:
        """Drain owned requests and close the session, even if the caller cancels."""
        if self._closed and (self._closing is None or self._closing.done()):
            if self._closing is not None:
                self._closing.result()
            return
        self._check_loop()
        self._closed = True
        if self._closing is None:
            self._closing = asyncio.create_task(self._cleanup())
            self._closing.add_done_callback(self._finished)
        await asyncio.shield(self._closing)

    async def _cleanup(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self._session is not None:
                await self._session.close()

    async def request(
        self,
        spec: RequestSpec[_ResponseT],
        *,
        request_timeout: float | None = None,
    ) -> _ResponseT:
        """Perform a bounded JSON request without retrying submission.

        Returns:
            The validated endpoint response.

        """
        self.check_open()
        seconds = self.timeout if request_timeout is None else request_timeout
        try:
            return await self._perform(self._request(spec, seconds), seconds)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            error = transport_error(
                spec.operation,
                exc,
                submission=spec.method == "POST" and spec.path == "jobs",
            )
            raise error from exc

    async def _request(
        self, spec: RequestSpec[_ResponseT], seconds: float
    ) -> _ResponseT:
        async with self._get_session().request(
            spec.method,
            self._http_url(spec.path),
            params=spec.params,
            json=spec.json_body,
            headers=self._auth_headers if spec.authenticated else self.headers,
            timeout=aiohttp.ClientTimeout(total=seconds, ceil_threshold=float("inf")),
        ) as response:
            return validate_response(
                spec,
                response.status,
                await response.text(),
                response.headers.get("content-type", ""),
                response.headers.get("Retry-After"),
            )

    async def download(
        self, route: str, path: str | os.PathLike[str], *, operation: str
    ) -> None:
        """Stream a result using the same session and request budget."""
        self.check_open()
        try:
            await self._perform(self._download(route, path, operation), self.timeout)
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            error = transport_error(operation, exc)
            raise error from exc

    async def _download(
        self, route: str, path: str | os.PathLike[str], operation: str
    ) -> None:
        async with self._get_session().get(
            self._http_url(route),
            headers=self._auth_headers,
            timeout=aiohttp.ClientTimeout(
                total=self.timeout, ceil_threshold=float("inf")
            ),
        ) as response:
            if download_needs_text(response.status):
                validate_download(
                    response.status,
                    await response.text(),
                    response.headers.get("Retry-After"),
                    operation=operation,
                )
            async with aiofiles.open(path, "wb") as file:
                async for chunk in response.content.iter_chunked(65536):
                    await file.write(chunk)


def transport_error(
    operation: str, exc: BaseException, *, submission: bool = False
) -> DownloadError:
    """Translate transport failures consistently across client modes.

    Returns:
        An error with retry classification and submission uncertainty.
    """
    message = f"Failed to {operation}: request error: {exc}"
    if submission:
        message += (
            " The job may have been accepted; another submission may duplicate it."
        )
    return DownloadError(
        message,
        retryable=isinstance(exc, (aiohttp.ClientConnectionError, TimeoutError))
        and not isinstance(
            exc,
            (
                aiohttp.ClientSSLError,
                aiohttp.ServerFingerprintMismatch,
                aiohttp.InvalidURL,
            ),
        ),
    )
