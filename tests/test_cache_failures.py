"""Cache policy owns storage failures without hiding execution failures."""

from asyncio import CancelledError
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pandas as pd
import pytest

from lyra.runtime.cache import TableCache
from test_support.location_schema import locations
from tests.cache_helpers import SCHEMA


@pytest.fixture(params=["metric", "location"])
def cache_io(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[TableCache, Callable, Callable, str, str]:
    cache = TableCache(tmp_path)
    if request.param == "location":
        return (
            cache,
            lambda value: cache.write_locations("key", locations({"value": value})),
            cache.read_locations,
            "read_locations",
            "write_locations",
        )
    return (
        cache,
        lambda value: cache.write(
            "key", {"value": pd.DataFrame({"value": [value]})}, {"value": SCHEMA}
        ),
        cache.read,
        "read",
        "write",
    )


@pytest.mark.parametrize("failure", ["lookup", "artifact", "validation"])
def test_read_failure_has_one_policy_diagnostic(
    cache_io: tuple[TableCache, Callable, Callable, str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    cache, write, read, store_read, _ = cache_io
    assert read("key") is None
    assert not caplog.records
    assert write(1) is not None
    validate = None
    error = OSError("unavailable")
    if failure == "lookup":
        monkeypatch.setattr(Path, "read_text", Mock(side_effect=error))
    elif failure == "artifact":
        monkeypatch.setattr(cache.store, store_read, Mock(side_effect=error))
    else:
        validate = Mock(side_effect=ValueError("invalid cached value"))
    assert read("key", validate) is None
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "lyra.runtime.cache"
    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].exc_info is not None


@pytest.mark.parametrize("failure", ["artifact", "lookup"])
def test_publication_failure_preserves_previous_lookup(
    cache_io: tuple[TableCache, Callable, Callable, str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    cache, write, read, store_read, store_write = cache_io
    old = write(1)
    assert old is not None
    expected = read("key")
    assert expected is not None
    with monkeypatch.context() as patch:
        if failure == "artifact":
            patch.setattr(cache.store, store_write, Mock(side_effect=OSError("full")))
        else:
            patch.setattr(Path, "replace", Mock(side_effect=OSError("full")))
        new = write(2)
    assert cache.reference("key") == old
    current = read("key")
    assert current is not None
    assert current.reference == old
    if failure == "artifact":
        assert new is None
        assert {path.name for path in cache.store.directory.iterdir()} == {old}
    else:
        assert new is not None
        assert new != old
        assert getattr(cache.store, store_read)(new) is not None
        assert {path.name for path in cache.store.directory.iterdir()} == {old, new}
    assert not list(cache.directory.glob(".writing-*"))
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "lyra.runtime.cache"
    assert caplog.records[0].levelname == "WARNING"


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, CancelledError])
@pytest.mark.parametrize("boundary", ["read", "write", "lookup", "acquire", "release"])
def test_cache_does_not_swallow_interruption(
    cache_io: tuple[TableCache, Callable, Callable, str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[BaseException],
    boundary: str,
) -> None:
    cache, write, read, store_read, store_write = cache_io
    assert write(1) is not None
    error = error_type()
    if boundary in {"acquire", "release"}:
        lock = Mock()
        getattr(lock, boundary).side_effect = error
        monkeypatch.setattr("lyra.runtime.cache.FileLock", Mock(return_value=lock))
        with pytest.raises(error_type) as raised, cache.lock("key"):
            pass
    else:
        if boundary == "lookup":
            monkeypatch.setattr(Path, "replace", Mock(side_effect=error))
        else:
            monkeypatch.setattr(
                cache.store,
                store_read if boundary == "read" else store_write,
                Mock(side_effect=error),
            )
        with pytest.raises(error_type) as raised:
            read("key") if boundary == "read" else write(2)
    assert raised.value is error
    assert not caplog.records


def test_unlock_failure_preserves_handler_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock = Mock(release=Mock(side_effect=OSError("unlock failed")))
    monkeypatch.setattr("lyra.runtime.cache.FileLock", Mock(return_value=lock))
    error = ValueError("handler failed")
    with (
        pytest.raises(ValueError, match="handler failed") as raised,
        TableCache(tmp_path).lock("key"),
    ):
        raise error
    assert raised.value is error
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "lyra.runtime.cache"
    lock.release.assert_called_once()


def test_reader_finishes_after_lookup_replacement(
    cache_io: tuple[TableCache, Callable, Callable, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, write, read, store_read, _ = cache_io
    old = write(1)
    expected = read("key")
    assert old is not None
    assert expected is not None
    pinned = Event()
    resume = Event()
    original = getattr(cache.store, store_read)

    def pause_read(reference: str) -> object:
        assert reference == old
        pinned.set()
        assert resume.wait(timeout=10)
        return original(reference)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with monkeypatch.context() as patch:
            patch.setattr(cache.store, store_read, pause_read)
            future = pool.submit(read, "key")
            try:
                assert pinned.wait(timeout=10)
                new = write(2)
                assert new is not None
                assert new != old
                assert cache.reference("key") == new
            finally:
                resume.set()
            observed = future.result(timeout=10)
        assert observed is not None
        assert observed.reference == old
        if store_read == "read":
            pd.testing.assert_frame_equal(
                observed.value["value"], expected.value["value"]
            )
        else:
            assert observed.value == expected.value
    current = read("key")
    assert current is not None
    assert current.reference == new
