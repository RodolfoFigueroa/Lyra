"""Stage a complete Arrow file before committing HTTP response headers."""

import asyncio
from collections.abc import Generator
from contextlib import closing, suppress
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from starlette.responses import FileResponse, Response
from starlette.types import Receive, Scope, Send
from typing_extensions import override

from lyra.contracts.errors import ResultAccessError
from lyra.server.routes.retrieval_errors import retrieval_error_response


class TableResponse(Response):
    """Own the reader during staging and the completed file during transmission."""

    def __init__(self, stream: Generator[bytes]) -> None:
        """Accept an unstarted reader for the staging worker."""
        super().__init__(media_type="application/vnd.apache.arrow.stream")
        self.stream = stream

    def _stage(self, path: Path) -> None:
        with closing(self.stream), path.open("wb") as staged:
            staged.writelines(self.stream)

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "table.arrow"
            worker = asyncio.create_task(asyncio.to_thread(self._stage, path))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # AnyIO scopes and repeated Task.cancel() calls can interrupt
                # the join. The thread must release the reader and file first.
                with anyio.CancelScope(shield=True):
                    joined = asyncio.gather(worker, return_exceptions=True)
                    while not joined.done():
                        with suppress(asyncio.CancelledError):
                            await asyncio.shield(joined)
                raise
            except ResultAccessError as error:
                await retrieval_error_response(error)(scope, receive, send)
            else:
                await FileResponse(path, media_type=self.media_type)(
                    scope, receive, send
                )
