"""Streams and file observers shared by transport and public download tests."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TypedDict

import httpx
import pytest
from typing_extensions import Unpack, override

from lyra.api.client import httpx as client_httpx


class ChunkStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        observe: Callable[[int], None] = lambda _: None,
        failure: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.observe = observe
        self.failure = failure
        self.closed = False

    @override
    def __iter__(self) -> Iterator[bytes]:
        consumed = 0
        for chunk in self.chunks:
            self.observe(consumed)
            yield chunk
            consumed += len(chunk)
        self.observe(consumed)
        if self.failure is not None:
            raise self.failure

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk

    @override
    def close(self) -> None:
        self.closed = True

    @override
    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.close()


class TemporaryFileOptions(TypedDict):
    mode: str
    dir: Path
    delete: bool


@pytest.fixture
def destinations(monkeypatch: pytest.MonkeyPatch) -> list[IO[bytes]]:
    files: list[IO[bytes]] = []
    original_temporary = tempfile.NamedTemporaryFile

    def open_temporary(**options: Unpack[TemporaryFileOptions]) -> object:
        file = original_temporary(**options)
        files.append(file.file)
        return file

    @contextmanager
    def open_file(path: str | os.PathLike[str]) -> Generator[IO[bytes]]:
        with Path(path).open("wb") as file:
            files.append(file)
            yield file

    monkeypatch.setattr(client_httpx, "_open_destination", open_file)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", open_temporary)
    return files
