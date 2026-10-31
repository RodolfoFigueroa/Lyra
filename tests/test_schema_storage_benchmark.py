"""Fidelity tests and an offline storage benchmark; no production format changes."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import os
import platform
import sys
from importlib.metadata import version
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter
from typing import TYPE_CHECKING

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from geopandas.testing import assert_geodataframe_equal
from lyra.sdk.cache import TableCache

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.connection import Connection

MODES = ("current", "ipc-none", "ipc-lz4", "ipc-zstd", "parquet-snappy", "parquet-zstd")
KINDS = ("numeric", "mixed", "points", "polygons")


def fixture(kind: str, rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(914)
    data = {f"value_{i}": rng.normal(size=rows) for i in range(16)}
    if kind != "numeric":
        data = {
            "id": pd.Series([f"id-{i:09d}" for i in range(rows)], dtype="string"),
            "count": pd.Series(np.arange(rows), dtype="Int64"),
            "flag": pd.Series(np.arange(rows) % 2 == 0, dtype="boolean"),
            "label": pd.Series(
                [f"label-{i % 1000}" for i in range(rows)], dtype="string"
            ),
            "category": pd.Categorical.from_codes(
                np.arange(rows) % 3,
                categories=pd.Index(["a", "b", "c", "unused"]),
                ordered=True,
            ),
            "time": pd.date_range("2024-01-01", periods=rows, freq="ns", tz="UTC"),
            "date": pd.Series(
                pd.date_range("2024-01-01", periods=rows, freq="s"),
                dtype=pd.ArrowDtype(pa.date32()),
            ),
            "value": rng.normal(size=rows),
        }
    frame = pd.DataFrame(data)
    if kind != "numeric":
        frame.loc[
            frame.index[::7], ["count", "flag", "label", "date", "time", "category"]
        ] = None
    if kind in {"points", "polygons"}:
        geometry = shapely.points(
            rng.uniform(-100, 100, rows), rng.uniform(-50, 50, rows)
        )
        if kind == "polygons":
            geometry = shapely.buffer(geometry, 0.01, quad_segs=32)
        frame = gpd.GeoDataFrame(frame, geometry=geometry, crs=4326)
    return frame


def contract(frame: pd.DataFrame) -> pandera.DataFrameSchema:
    return pandera.DataFrameSchema(
        {
            name: pandera.Column(dtype, nullable=True)
            for name, dtype in frame.dtypes.items()
        },
        strict=True,
        ordered=True,
        checks=pandera.Check(lambda df: len(df) >= 0),
    )


def write(frame: pd.DataFrame, path: Path, mode: str) -> None:
    if mode == "current":
        cache = TableCache(path)
        with cache.lock("entry"):
            cache.write("entry", {"result": frame}, {"result": contract(frame)})
        return
    table = (
        pa.table(frame.to_arrow(index=False, geometry_encoding="WKB"))
        if isinstance(frame, gpd.GeoDataFrame)
        else pa.Table.from_pandas(frame, preserve_index=False)
    )
    path.mkdir(exist_ok=True)
    target = path / "table"
    codec = mode.split("-", 1)[1]
    if mode.startswith("parquet"):
        pq.write_table(table, target, compression=codec)
    else:
        options = pa.ipc.IpcWriteOptions(compression=None if codec == "none" else codec)
        with (
            pa.OSFile(str(target), "wb") as sink,
            pa.ipc.new_file(sink, table.schema, options=options) as writer,
        ):
            if table.num_rows:
                writer.write_table(table)
            else:
                # Standard Arrow batch API retains dictionaries for zero rows.
                writer.write_batch(
                    pa.RecordBatch.from_arrays(
                        [c.combine_chunks() for c in table.columns], schema=table.schema
                    )
                )


def read(path: Path, mode: str, *, spatial: bool) -> pd.DataFrame:
    if mode == "current":
        result = TableCache(path).read("entry")
        assert result is not None
        return result["result"]
    target = path / "table"
    if mode.startswith("parquet"):
        table = pq.read_table(target)
    else:
        with pa.OSFile(str(target), "rb") as source:
            table = pa.ipc.open_file(source).read_all()
    return gpd.GeoDataFrame.from_arrow(table) if spatial else table.to_pandas()


def equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    if isinstance(expected, gpd.GeoDataFrame):
        assert isinstance(actual, gpd.GeoDataFrame)
        assert_geodataframe_equal(actual, expected)
        np.testing.assert_array_equal(
            shapely.to_wkb(actual.geometry), shapely.to_wkb(expected.geometry)
        )
    else:
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    for name in expected.select_dtypes(include=np.floating):
        np.testing.assert_array_equal(
            np.signbit(actual[name].dropna().to_numpy(dtype=float)),
            np.signbit(expected[name].dropna().to_numpy(dtype=float)),
        )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", KINDS)
def test_populated_roundtrip(tmp_path: Path, mode: str, kind: str) -> None:
    frame = fixture(kind, 23)
    write(frame, tmp_path, mode)
    restored = read(tmp_path, mode, spatial=isinstance(frame, gpd.GeoDataFrame))
    equal(restored, frame)
    contract(frame).validate(restored)


@pytest.mark.parametrize("mode", MODES)
def test_numeric_boundaries(tmp_path: Path, mode: str) -> None:
    frame = pd.DataFrame(
        {
            "signed": pd.Series([-(2**63), 2**63 - 1, None], dtype="Int64"),
            "unsigned": pd.Series([0, 2**64 - 1, None], dtype="UInt64"),
            "zero": pd.Series([-0.0, 0.0, None], dtype="Float64"),
        }
    )
    write(frame, tmp_path, mode)
    equal(read(tmp_path, mode, spatial=False), frame)


def fidelity(path: Path) -> list[dict]:
    results = []
    for mode, kind, state in product(MODES, KINDS, ("populated", "empty", "all-null")):
        frame = fixture(kind, 23 if state != "empty" else 0)
        if state == "all-null":
            for name in frame:
                if name != "geometry":
                    frame[name] = frame[name].where(
                        np.zeros(len(frame), dtype=bool), None
                    )
        destination = path / f"{mode}-{kind}-{state}"
        try:
            write(frame, destination, mode)
            actual = read(
                destination, mode, spatial=isinstance(frame, gpd.GeoDataFrame)
            )
            equal(actual, frame)
            contract(frame).validate(actual)
            error = None
        except (AssertionError, ValueError, TypeError) as exc:
            error = str(exc)
        results.append({"mode": mode, "kind": kind, "state": state, "error": error})
    return results


def rss() -> int:
    fields = Path("/proc/self/statm").read_text(encoding="utf-8").split()
    return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")


def memory(action: Callable) -> float:
    gc.collect()
    baseline = rss()
    samples = [baseline]
    stop = Event()

    def sample() -> None:
        while not stop.wait(0.001):
            samples.append(rss())

    worker = Thread(target=sample)
    worker.start()
    result = action()
    samples.append(rss())
    stop.set()
    worker.join()
    del result
    return max(0, max(samples) - baseline) / 2**20


def evict(path: Path) -> None:
    for member in path.rglob("*"):
        if member.is_file() and not member.is_symlink():
            with member.open("rb") as stream:
                os.fsync(stream.fileno())
                os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def measure(kind: str, rows: int, mode: str, path: Path) -> dict:
    frame = fixture(kind, rows)
    schema = contract(frame)
    schema.validate(frame)
    spatial = isinstance(frame, gpd.GeoDataFrame)
    target = path / "initial"
    write_memory = memory(lambda: write(frame, target, mode))
    read_memory = memory(lambda: schema.validate(read(target, mode, spatial=spatial)))
    equal(read(target, mode, spatial=spatial), frame)
    timings: dict[str, list[float]] = {
        k: [] for k in ("write", "warm_read", "validated_read", "advised_read")
    }
    for repeat in range(5):
        operations = {
            "write": lambda repeat=repeat: write(
                frame, path / f"repeat-{repeat}", mode
            ),
            "warm_read": lambda: read(target, mode, spatial=spatial),
            "validated_read": lambda: schema.validate(
                read(target, mode, spatial=spatial)
            ),
            "advised_read": lambda: read(target, mode, spatial=spatial),
        }
        # Warm OS cache explicitly before both warm read measurements.
        for phase, action in operations.items():
            if phase == "advised_read":
                evict(target)
            elif phase in {"warm_read", "validated_read"}:
                read(target, mode, spatial=spatial)
            start = perf_counter()
            result = action()
            timings[phase].append((perf_counter() - start) * 1000)
            del result
    return {
        "kind": kind,
        "rows": rows,
        "mode": mode,
        "timings_ms": timings,
        "bytes": sum(
            p.stat().st_size
            for p in target.rglob("*")
            if p.is_file() and not p.is_symlink()
        ),
        "write_incremental_rss_mib": write_memory,
        "validated_read_incremental_rss_mib": read_memory,
    }


def child(kind: str, rows: int, mode: str, connection: Connection) -> None:
    with TemporaryDirectory(prefix=".lyra-storage-case-", dir=Path.cwd()) as temporary:
        connection.send(measure(kind, rows, mode, Path(temporary)))
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", nargs=3)
    args = parser.parse_args()
    with TemporaryDirectory(prefix=".lyra-storage-", dir=Path.cwd()) as temporary:
        path = Path(temporary)
        if args.case:
            kind, rows, mode = args.case
            sys.stdout.write(json.dumps(measure(kind, int(rows), mode, path)) + "\n")
            return
        report = {
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "libraries": {
                    name: version(name)
                    for name in ("pandas", "pyarrow", "pandera", "geopandas")
                },
                "arrow_threads": pa.cpu_count(),
            },
            "fidelity": fidelity(path),
            "measurements": [],
        }
        for kind in KINDS:
            for rows in (100, 10000, 100000 if kind != "polygons" else 20000):
                for mode in MODES:
                    context = multiprocessing.get_context("spawn")
                    receiver, sender = context.Pipe(duplex=False)
                    process = context.Process(
                        target=child, args=(kind, rows, mode, sender)
                    )
                    process.start()
                    sender.close()
                    result = receiver.recv()
                    process.join()
                    receiver.close()
                    assert process.exitcode == 0
                    report["measurements"].append(result)
                    args.output.write_text(
                        json.dumps(report, indent=2), encoding="utf-8"
                    )
                    sys.stdout.write(f"Completed {kind} {rows} {mode}\n")
                    sys.stdout.flush()


if __name__ == "__main__":
    main()
