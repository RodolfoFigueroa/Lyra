"""Streaming Arrow responses with preflight validation and owned cleanup."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack
from tempfile import TemporaryFile
from typing import BinaryIO

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send
from typing_extensions import override

from lyra.contracts.errors import ResultAccessError
from lyra.server.routes.retrieval_errors import retrieval_error_response


class TableResponse(StreamingResponse):
    """Own a synchronous Arrow reader through completion or client disconnect."""

    def __init__(self, stream: Generator[bytes], *, verify: bool = False) -> None:
        """Accept an unstarted reader; acquire resources only while serving."""
        self.verify = verify
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

    async def advance(self, staged: BinaryIO | None = None) -> bytes | None:
        """Return the next chunk, awaiting worker ownership before cancellation.

        Native asyncio task cancellation can interrupt even a shielded AnyIO
        worker await. Keep that worker alive and join it before closing its reader.

        Raises:
            asyncio.CancelledError: After the in-flight reader relinquishes ownership.
        """

        def read() -> bytes | None:
            chunk = next(self.stream, None)
            if chunk is not None and staged is not None:
                staged.write(chunk)
            return chunk

        worker = asyncio.create_task(asyncio.to_thread(read))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await asyncio.gather(worker, return_exceptions=True)
            raise

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            with ExitStack() as files:
                try:
                    if self.verify:
                        staged = files.enter_context(TemporaryFile())
                        if not await self.stage(staged, receive):
                            return
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

    async def stage(self, staged: BinaryIO, receive: Receive) -> bool:
        """Encode selected batches, stopping when the client disconnects.

        Returns:
            Whether the entire stream was staged for transmission.

        """
        completed = False
        failure = None
        async with anyio.create_task_group() as group:

            async def watch_disconnect() -> None:
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()

            group.start_soon(watch_disconnect)
            try:
                while await self.advance(staged) is not None:
                    pass
                completed = True
            except ResultAccessError as error:
                failure = error
            finally:
                group.cancel_scope.cancel()

        if failure is not None:
            raise failure
        if completed:
            staged.seek(0)
            self.stream = file_chunks(staged)
        return completed


def file_chunks(staged: BinaryIO) -> Generator[bytes]:
    """Yield bounded blocks from a fully encoded Arrow stream."""
    while chunk := staged.read(64 * 1024):
        yield chunk
