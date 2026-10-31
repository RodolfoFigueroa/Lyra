import json
import multiprocessing
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import Dependency, LocalRunner, PluginDefinition, ipc_codec, metric
from lyra.sdk.cache import TableCache
from lyra.sdk.errors import MetricResultError
from lyra.sdk.results import normalize_native_result
from shapely.geometry import Point

from benchmarks.schema_storage.fixtures import declarations
from benchmarks.schema_storage.fixtures import frame as fixture_frame

SCHEMA = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}
)


def write(cache: TableCache, key: str, frames: dict) -> None:
    schemas = {
        name: pa.DataFrameSchema(
            {
                column: pa.Column(series.dtype, nullable=True)
                for column, series in frame.items()
            }
        )
        for name, frame in frames.items()
    }
    cache.write(key, frames, schemas)


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


@pytest.mark.parametrize("values", [[1, 2], ["a", "b"]])
def test_categories_roundtrip(tmp_path: Path, values: list) -> None:
    frame = pd.DataFrame(
        {"value": pd.Categorical(values, ordered=True)},
        index=pd.CategoricalIndex(values, ordered=True, name="id"),
    )
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        write(cache, "key", {"table": frame})
    loaded = cache.read("key")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded["table"], frame.reset_index(drop=True))


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
        write(cache, "key", frames)
    loaded = cache.read("key")
    assert loaded is not None
    for name, frame in frames.items():
        pd.testing.assert_frame_equal(loaded[name], frame)
    assert loaded["spatial"].crs == frames["spatial"].crs


def test_geometry_only_bundle(tmp_path: Path) -> None:
    frame = gpd.GeoDataFrame(geometry=[Point(1, 2), Point(3, 4)], crs=4326)
    cache = TableCache(tmp_path)
    with cache.lock("geometry"):
        write(cache, "geometry", {"result": frame})
    restored = cache.read("geometry")
    assert restored is not None
    pd.testing.assert_frame_equal(restored["result"], frame)
    assert restored["result"].crs == frame.crs


@pytest.mark.parametrize("index", [[1, "text"], [object(), object()]])
def test_incidental_index_is_discarded(index: list) -> None:
    result = normalize_native_result(
        "source", {"source": SCHEMA}, pd.DataFrame({"value": [1, 2]}, index=index)
    )
    pd.testing.assert_frame_equal(result["source"], pd.DataFrame({"value": [1, 2]}))


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
    write(cache, "key", {"table": pd.DataFrame()})
    assert cache.read("key") is None


def _concurrent_writer(directory: Path) -> None:
    cache = TableCache(directory)
    with cache.lock("shared") as acquired:
        assert acquired
        if cache.read("shared") is None:
            with (directory / "calculations").open("a") as log:
                log.write("calculated\n")
            write(cache, "shared", {"value": pd.DataFrame({"value": [1]})})


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
        write(cache, "typed", {"value": frame})
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
        write(cache, "multi", {"table": frame})
    loaded = cache.read("multi")
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded["table"], frame.reset_index(drop=True))


@pytest.mark.parametrize("failure", [ValueError, TypeError, NotImplementedError])
def test_serialization_failure_does_not_publish_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    cache = TableCache(tmp_path)
    monkeypatch.setattr(ipc_codec, "encode", Mock(side_effect=failure("cannot encode")))
    with cache.lock("key"):
        write(cache, "key", {"table": pd.DataFrame({"value": [1]})})
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


@pytest.mark.parametrize("failure", ["stage", "publish"])
def test_replacement_preserves_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    cache = TableCache(tmp_path)
    old = {"table": pd.DataFrame({"value": [1]})}
    new = {"table": pd.DataFrame({"value": [2]})}
    with cache.lock("key"):
        write(cache, "key", old)
        pinned = (tmp_path / "key").resolve()
        if failure == "stage":
            monkeypatch.setattr(ipc_codec, "encode", Mock(side_effect=OSError("full")))
        else:
            original = Path.replace

            def fail_publish(path: Path, target: Path) -> Path:
                loaded = cache.read("key")
                assert loaded is not None
                pd.testing.assert_frame_equal(loaded["table"], old["table"])
                if target == tmp_path / "key":
                    message = "publication failed"
                    raise OSError(message)
                return original(path, target)

            monkeypatch.setattr(Path, "replace", fail_publish)
        write(cache, "key", new)
        loaded = cache.read("key")
        assert loaded is not None
        pd.testing.assert_frame_equal(loaded["table"], old["table"])
        monkeypatch.undo()
        write(cache, "key", new)
        pd.testing.assert_frame_equal(
            ipc_codec.decode((pinned / "0.arrow").read_bytes()).to_frame(), old["table"]
        )
        loaded = cache.read("key")
        assert loaded is not None
        pd.testing.assert_frame_equal(loaded["table"], new["table"])


@pytest.mark.parametrize("damage", ["missing", "corrupt", "version", "kind", "path"])
def test_invalid_table_bundle(tmp_path: Path, damage: str) -> None:
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        write(cache, "key", {"table": pd.DataFrame({"value": [1]})})
    directory = tmp_path / "key"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if damage == "missing":
        (directory / "0.arrow").unlink()
    elif damage == "corrupt":
        (directory / "0.arrow").write_bytes(b"broken")
    else:
        field, value = {
            "version": ("version", 999),
            "kind": ("kind", "location_operation"),
            "path": ("members", ["../0.arrow"]),
        }[damage]
        manifest[field] = value
        manifest_path.write_text(json.dumps(manifest))
    assert cache.read("key") is None


def test_manifest_symlink_is_rejected_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TableCache(tmp_path)
    with cache.lock("key"):
        write(cache, "key", {"table": pd.DataFrame({"value": [1]})})
    manifest = tmp_path / "key" / "manifest.json"
    outside = tmp_path / "outside.json"
    manifest.replace(outside)
    manifest.symlink_to(outside)
    read_text = Mock(side_effect=AssertionError("Unsafe manifest was opened"))
    monkeypatch.setattr(Path, "read_text", read_text)
    assert cache.read("key") is None
    read_text.assert_not_called()


@pytest.mark.parametrize("state", ["populated", "empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_declared_fixture_bundle(tmp_path: Path, state: str, *, spatial: bool) -> None:

    schema = declarations(spatial=spatial).remove_columns(["category"])
    expected = fixture_frame(state, spatial=spatial).drop(columns="category")
    cache = TableCache(tmp_path)
    with cache.lock("matrix"):
        cache.write(
            "matrix",
            {"first": expected, "second": expected},
            {
                "first": schema,
                "second": schema,
            },
        )
    restored = cache.read("matrix")
    assert restored is not None
    assert list(restored) == ["first", "second"]
    for result in restored.values():
        pd.testing.assert_frame_equal(result, expected.reset_index(drop=True))
        if spatial:
            assert result.crs == expected.crs


def test_executable_check_rejects_valid_artifact(tmp_path: Path) -> None:
    allowed = [True]
    calls = []
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})},
        checks=pa.Check(
            lambda frame: allowed[0] or frame.value.eq(2).all(), name="current_value"
        ),
    )

    @metric(name="checked", description="Checked", outputs={"checked": schema})
    def checked() -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame({"value": [1 if allowed[0] else 2]})

    plugin = PluginDefinition(metrics=[checked])
    runner = LocalRunner.from_definitions({"example": plugin}, cache_dir=tmp_path)
    assert runner.run("checked", {})["checked"].value.tolist() == [1]
    allowed[0] = False
    assert runner.run("checked", {})["checked"].value.tolist() == [2]
    assert runner.run("checked", {})["checked"].value.tolist() == [2]
    assert len(calls) == 2
