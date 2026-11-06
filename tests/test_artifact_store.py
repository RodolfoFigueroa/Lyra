"""Immutable artifacts remain accessible independently of computation lookups."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.cache import TableCache
from tests.cache_helpers import cached_value


@pytest.fixture
def schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema({"value": pa.Column(int)})


def test_artifact_survives_cache_replacement_and_clear(
    tmp_path: Path, schema: pa.DataFrameSchema
) -> None:
    cache = TableCache(tmp_path)
    original = {"table": pd.DataFrame({"value": [1]})}
    replacement = {"table": pd.DataFrame({"value": [2]})}
    with cache.lock("invocation"):
        cache.write("invocation", original, {"table": schema})
        retained = cache.reference("invocation")
        assert retained is not None
        cache.write("invocation", replacement, {"table": schema})
    current = cache.reference("invocation")
    assert current is not None
    assert current != retained
    (cache.directory / "invocation").unlink()
    assert cached_value(cache.read("invocation")) is None
    pd.testing.assert_frame_equal(
        cache.store.read(retained)["table"], original["table"]
    )
    pd.testing.assert_frame_equal(
        cache.store.read(current)["table"], replacement["table"]
    )


def test_artifact_publication_failure_is_not_a_success(
    tmp_path: Path, schema: pa.DataFrameSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(
        "lyra.data.ipc_codec.encode", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        store.write({"table": pd.DataFrame({"value": [1]})}, {"table": schema})
    assert list(store.directory.iterdir()) == []


@pytest.mark.parametrize("identifier", ["../outside", "/outside", "x" * 32, ""])
def test_artifact_reference_cannot_escape_store(
    tmp_path: Path, identifier: str
) -> None:
    with pytest.raises(ValueError, match="Invalid artifact identifier"):
        ArtifactStore(tmp_path).read(identifier)


def test_missing_and_corrupt_artifacts_raise(
    tmp_path: Path, schema: pa.DataFrameSchema
) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("0" * 32)
    reference = store.write({"table": pd.DataFrame({"value": [1]})}, {"table": schema})
    (store.directory / reference / "0.arrow").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Invalid typed IPC artifact"):
        store.read(reference)


@pytest.mark.parametrize("saved", ["{", "{}", '{"table": {"metadata": null}}'])
def test_corrupt_inspection_is_a_cache_miss(
    tmp_path: Path, schema: pa.DataFrameSchema, saved: str
) -> None:
    cache = TableCache(tmp_path)
    reference = cache.write(
        "invocation", {"table": pd.DataFrame({"value": [1]})}, {"table": schema}
    )
    assert reference is not None
    (cache.store.directory / reference / "inspection.json").write_text(saved)
    with pytest.raises(ValueError, match=r"validation error|Inspection output names"):
        cache.store.inspect(reference)
    assert cache.read("invocation") is None
