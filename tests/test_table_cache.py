import multiprocessing
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import Dependency, LocalRunner, PluginDefinition, metric
from lyra.sdk.cache import TableCache
from lyra.sdk.errors import MetricResultError
from lyra.sdk.results import normalize_native_result
from shapely.geometry import Point

SCHEMA = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)


def test_dependency_reused_across_runners(tmp_path: Path) -> None:
    calls = []

    @metric(name="source", description="Source", outputs={"source": SCHEMA})
    def source() -> pd.DataFrame:
        calls.append("source")
        return pd.DataFrame({"value": [1]})

    @metric(
        name="consumer",
        description="Consumer",
        outputs={"consumer": SCHEMA},
        inputs={"data": Dependency("source")},
    )
    def consumer(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("consumer")
        return data

    plugin = PluginDefinition(metrics=[source, consumer])
    for name in ("source", "consumer", "consumer"):
        runner = LocalRunner.from_definitions({"example": plugin}, cache_dir=tmp_path)
        assert runner.run(name, {})[name].value.tolist() == [1]
    assert calls == ["source", "consumer"]


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("values", [[1, 2], ["a", "b"], [date(2020, 1, 1)]])
def test_categories_roundtrip(tmp_path: Path, values: list, *, empty: bool) -> None:
    frame = pd.DataFrame(
        {"value": pd.Categorical(values, ordered=True)},
        index=pd.CategoricalIndex(values, ordered=True, name="id"),
    )
    if empty:
        frame = frame.iloc[:0]
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        cache.write("key", {"table": frame})
    loaded = cache.read("key")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded["table"], frame)


def test_spatial_and_nullable_bundle(tmp_path: Path) -> None:
    frames = {
        "plain": pd.DataFrame(
            {
                "integer": pd.Series([1, None], dtype="Int64"),
                "text": pd.Series(["a", None], dtype="string"),
            }
        ),
        "spatial": gpd.GeoDataFrame({"value": [1]}, geometry=[Point(1, 2)], crs=3857),
    }
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        cache.write("key", frames)
    loaded = cache.read("key")
    assert loaded is not None
    for name, frame in frames.items():
        pd.testing.assert_frame_equal(loaded[name], frame)
    assert loaded["spatial"].crs == frames["spatial"].crs


@pytest.mark.parametrize("index", [[1, "text"], [object(), object()]])
def test_unsupported_index(index: list) -> None:
    with pytest.raises(MetricResultError, match="scalar"):
        normalize_native_result(
            "source", {"source": SCHEMA}, pd.DataFrame({"value": [1, 2]}, index=index)
        )


def test_mixed_categories() -> None:
    schema = pa.DataFrameSchema(
        {"value": pa.Column("category", description="Value", metadata={"unit": None})}
    )
    with pytest.raises(MetricResultError, match="scalar"):
        normalize_native_result(
            "source",
            {"source": schema},
            pd.DataFrame({"value": pd.Categorical([1, "text"])}),
        )


def test_unreadable_and_failed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TableCache(tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "manifest.json").write_text("invalid")
    assert cache.read("broken") is None
    monkeypatch.setattr(cache, "_publish", Mock(side_effect=OSError("disk full")))
    cache.write("key", {"table": pd.DataFrame()})
    assert cache.read("key") is None


def _concurrent_writer(directory: Path) -> None:
    cache = TableCache(directory)
    with cache.lock("shared") as acquired:
        assert acquired
        if cache.read("shared") is None:
            with (directory / "calculations").open("a") as log:
                log.write("calculated\n")
            cache.write("shared", {"value": pd.DataFrame({"value": [1]})})


def test_processes_share_one_calculation(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(target=_concurrent_writer, args=(tmp_path,)) for _ in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.kill()
            process.join()
            pytest.fail("Cache lock did not release")
        assert process.exitcode == 0
    assert (tmp_path / "calculations").read_text() == "calculated\n"


@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "uint64",
        "float16",
        "float32",
        "Int64",
        "Float64",
        "boolean",
        "string",
        "string[python]",
        "datetime64[ns, UTC]",
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_dtype_roundtrip(tmp_path: Path, dtype: str, *, empty: bool) -> None:
    value = True if dtype == "boolean" else 1
    frame = pd.DataFrame({"value": pd.Series([value], dtype=dtype)})
    if empty:
        frame = frame.iloc[:0]
    cache = TableCache(tmp_path)
    with cache.lock("typed"):
        cache.write("typed", {"value": frame})
    loaded = cache.read("typed")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded["value"], frame)


def test_multiindex_roundtrip(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {"value": pd.Series(["a", "b"], dtype=object).array},
        index=pd.MultiIndex.from_arrays(
            [
                pd.CategoricalIndex([2, 1], categories=[1, 2, 3], ordered=True),
                pd.Index(["a", "b"], dtype=object),
            ],
            names=["id", "label"],
        ),
    )
    cache = TableCache(tmp_path)
    with cache.lock("multi"):
        cache.write("multi", {"table": frame})
    loaded = cache.read("multi")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded["table"], frame)


@pytest.mark.parametrize("failure", [ValueError, TypeError, NotImplementedError])
def test_serialization_failure_does_not_publish_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    cache = TableCache(tmp_path)
    monkeypatch.setattr(cache, "_metadata", Mock(side_effect=failure("cannot encode")))
    with cache.lock("key"):
        cache.write("key", {"table": pd.DataFrame({"value": [1]})})
    assert cache.read("key") is None
    assert not list(tmp_path.glob(".writing-*"))
    assert not (tmp_path / "key").exists()


def test_lock_creation_failure_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "lyra.sdk.cache.FileLock", Mock(side_effect=NotImplementedError("no locks"))
    )
    with TableCache(tmp_path).lock("key") as acquired:
        assert acquired is False
