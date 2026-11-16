"""Streaming Arrow responses with preflight validation and owned cleanup."""

import asyncio
from collections.abc import AsyncGenerator, Generator

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send
from typing_extensions import override

from lyra.contracts.errors import ResultAccessError
from lyra.server.routes.retrieval_errors import retrieval_error_response


class TableResponse(StreamingResponse):
    """Own a synchronous Arrow reader through completion or client disconnect."""

    def __init__(self, stream: Generator[bytes]) -> None:
        """Accept an unstarted reader; acquire resources only while serving."""
        self.stream = stream
        self.first: bytes | None = None
        self.iterator = self.chunks()
        super().__init__(
            self.iterator, media_type="application/vnd.apache.arrow.stream"
        )

    async def chunks(self) -> AsyncGenerator[bytes]:
        """Yield preflight bytes followed by offloaded, bounded row batches."""
        if self.first is not None:
            yield self.first
            self.first = None
        while (chunk := await self.advance()) is not None:
            yield chunk

    async def advance(self) -> bytes | None:
        """Return the next chunk, awaiting worker ownership before cancellation.

        Native asyncio task cancellation can interrupt even a shielded AnyIO
        worker await. Keep that worker alive and join it before closing its reader.

        Raises:
            asyncio.CancelledError: After the in-flight reader relinquishes ownership.
        """
        worker = asyncio.create_task(asyncio.to_thread(lambda: next(self.stream, None)))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await asyncio.gather(worker, return_exceptions=True)
            raise

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            try:
                # Do not abandon a worker that may still own the SQL connection.
                self.first = await self.advance()
            except ResultAccessError as error:
                await retrieval_error_response(error)(scope, receive, send)
            else:
                await super().__call__(scope, receive, send)
        finally:
            # Starlette can cancel its streaming task on disconnect. Shield close
            # so the suspended generator, writer and SQL selection still unwind.
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.to_thread.run_sync(self.stream.close)
                finally:
                    await self.iterator.aclose()
