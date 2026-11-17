"""Observe resource ownership at the GeoParquet publication boundary."""

from pathlib import Path
from typing import IO
from unittest.mock import Mock

import geopandas as gpd
import httpx
import pytest

from lyra.data import geoparquet


def observe_publication(
    monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response]
) -> None:
    files: list[IO[bytes]] = []
    create = geoparquet.tempfile.NamedTemporaryFile
    replace = Path.replace

    def temporary(**kwargs: str | Path | bool) -> IO[bytes]:
        file = create(dir=Path(str(kwargs["dir"])), delete=bool(kwargs["delete"]))
        files.append(file)
        return file

    def publish(source: Path, target: str | Path) -> Path:
        assert files
        assert all(file.closed for file in files)
        if Path(target).name == "remote.parquet":
            assert all(response.is_closed for response in responses)
            assert Path(target).read_bytes() == b"old"
        return replace(source, target)

    monkeypatch.setattr(geoparquet.tempfile, "NamedTemporaryFile", temporary)
    monkeypatch.setattr(Path, "replace", publish)


def inject_failure(
    failure: str, error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_self: gpd.GeoDataFrame, sink: IO[bytes], **_kwargs: object) -> None:
        sink.write(b"partial")
        raise error

    if failure == "close":
        original = geoparquet.tempfile.NamedTemporaryFile

        def failing_close(**kwargs: str | Path | bool) -> IO[bytes]:
            file = original(dir=Path(str(kwargs["dir"])), delete=bool(kwargs["delete"]))
            close = file.close

            def finish() -> None:
                close()
                raise error

            monkeypatch.setattr(file, "close", finish)
            return file

        monkeypatch.setattr(geoparquet.tempfile, "NamedTemporaryFile", failing_close)
    elif failure == "conversion":
        monkeypatch.setattr(geoparquet, "prepare_geoparquet", Mock(side_effect=error))
    elif failure == "replace":
        monkeypatch.setattr(Path, "replace", Mock(side_effect=error))
    elif failure == "denied":
        monkeypatch.setattr(
            geoparquet.tempfile, "NamedTemporaryFile", Mock(side_effect=error)
        )
    elif failure != "parent":
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", fail_write)
