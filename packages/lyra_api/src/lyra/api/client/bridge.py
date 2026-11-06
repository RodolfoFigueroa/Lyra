"""Client-owned loop thread for synchronous access to the shared transport."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from concurrent.futures import Future
    from typing import Any

    from lyra.api.client.http import HTTPTransport

_ResponseT = TypeVar("_ResponseT")


class TransportBridge:
    """Own a lazy loop thread until explicit client close."""

    def __init__(self, transport: HTTPTransport) -> None:
        """Initialize without starting a thread."""
        self._transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._closing: Future[None] | None = None

    def check_open(self) -> None:
        """Reject operations on a closed client.

        Raises:
            RuntimeError: If the client has closed.
        """
        if self._closed:
            msg = "Client is closed."
            raise RuntimeError(msg)

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    def call(
        self, operation: Coroutine[Any, Any, _ResponseT], *, seconds: float
    ) -> _ResponseT:
        """Bound caller waiting independently of asynchronous request cleanup.

        Returns:
            The transport result.
        """
        deadline = time.monotonic() + seconds
        try:
            with self._lock:
                self.check_open()
                loop = self._start()
                future = asyncio.run_coroutine_threadsafe(operation, loop)
        except BaseException:
            operation.close()
            raise
        try:
            return future.result(timeout=max(0.0, deadline - time.monotonic()))
        finally:
            if not future.done():
                future.cancel()

    def close(self) -> None:
        """Drain the transport, stop its loop, and join its thread."""
        with self._lock:
            self._closed = True
            if self._loop is None:
                return
            loop, thread = self._loop, self._thread
            if self._closing is None:
                self._closing = asyncio.run_coroutine_threadsafe(
                    self._transport.aclose(), loop
                )
                # Cleanup and loop shutdown survive interruption of the closing caller.
                self._closing.add_done_callback(
                    lambda _: loop.call_soon_threadsafe(loop.stop)
                )
            closing = self._closing
        try:
            closing.result()
        finally:
            if closing.done() and thread is not None:
                thread.join()
                with self._lock:
                    self._loop = None
                    self._thread = None

    def _start(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run, args=(loop,), name="lyra-http", daemon=True
            )
            try:
                thread.start()
            except BaseException:
                loop.close()
                raise
            self._loop, self._thread = loop, thread
        return self._loop
