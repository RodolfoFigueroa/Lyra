from datetime import date
from pathlib import Path
from typing import cast

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
from lyra.sdk.cache import TableCache
from lyra.sdk.execution import GraphExecutor

from benchmarks.metric_representation.execution import (
    assert_equal,
    make_executor,
)
from benchmarks.metric_representation.storage import MODES, Mode, ResultStore
from benchmarks.metric_representation.workloads import (
    SHAPES,
    Shape,
    build_workload,
    synthetic,
)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True])
def test_scalar_contract(mode: Mode, *, empty: bool) -> None:
    frame = pd.DataFrame(
        {
            "half": pd.Series([1, None], dtype="float16"),
            "unsigned": pd.Series([2**63 + 1, 2**63 + 2], dtype="uint64"),
            "nullable": pd.Series([1, None], dtype="Int64"),
            "float": pd.Series([1, None], dtype="Float64"),
            "boolean": pd.Series([True, None], dtype="boolean"),
            "python_string": pd.Series(["a", None], dtype="string[python]"),
            "arrow_string": pd.Series(["b", None], dtype="string[pyarrow]"),
            "object": pd.Series(["c", None], dtype=object),
            "dates": [date(2020, 1, 1), None],
            "timestamp": pd.date_range(
                "2020-01-01", periods=2, tz="America/Mexico_City"
            ),
            "category": pd.Categorical([2, 1], categories=[1, 2, 3], ordered=True),
        }
    )
    frame.index = pd.MultiIndex.from_arrays(
        [
            pd.CategoricalIndex([2, 1], categories=[1, 2, 3], ordered=True),
            pd.Index(["a", "b"], dtype=object),
        ],
        names=["category_index", "object_index"],
    )
    if empty:
        frame = frame.iloc[:0]
    store = ResultStore(mode)
    store.put("key", {"table": frame})
    assert_equal(store.get("key")["table"], frame)


@pytest.mark.parametrize("mode", MODES)
def test_spatial_contract(mode: Mode) -> None:
    frame = gpd.GeoDataFrame(
        {"value": [1, 2, 3, 4]},
        geometry=shapely.from_wkt(
            ["POINT Z (1 2 3)", "POINT EMPTY", None, "LINESTRING (1 2, 3 4)"]
        ),
        crs=3857,
        index=pd.Index([7, 4, 8, 1], name="row"),
    )
    store = ResultStore(mode)
    store.put("key", {"table": frame})
    assert_equal(store.get("key")["table"], frame)


@pytest.mark.parametrize("mode", ["isolated", "arrow"])
def test_producer_and_consumer_mutation_isolation(mode: Mode) -> None:
    array = np.arange(6.0)
    indices = np.array([10, 11, 12, 13, 14, 15])
    frame = pd.DataFrame({"value": array}, index=indices, copy=False)
    expected = frame.copy(deep=True)
    expected.index = frame.index.copy(deep=True)
    store = ResultStore(mode)
    store.put("key", {"table": frame})
    array[0] = 1000
    indices[0] = 1000
    first = store.get("key")["table"]
    assert_equal(first, expected)
    first.iloc[0, 0] = -1000
    first.index += 100
    assert_equal(store.get("key")["table"], expected)


def test_native_baseline_shares_the_original_frame() -> None:
    frame = pd.DataFrame({"value": [1]})
    store = ResultStore("native")
    store.put("key", {"table": frame})
    assert store.get("key")["table"] is frame


@pytest.mark.parametrize("mode", ["isolated", "arrow"])
@pytest.mark.parametrize(
    ("column", "value"),
    [("category", "b"), ("nullable", 2), ("flag", False), ("label", "changed")],
)
def test_extension_columns_are_mutable_exports(
    mode: Mode, column: str, *, value: str | int | bool
) -> None:
    source = synthetic("mixed", 4)
    store = ResultStore(mode)
    store.put("key", {"table": source})
    consumer = store.get("key")["table"]
    consumer.iloc[0, list(consumer.columns).index(column)] = value
    assert consumer[column].iloc[0] == value
    assert_equal(store.get("key")["table"], source)


@pytest.mark.parametrize("mode", ["isolated", "arrow"])
def test_geometry_exports_are_mutable_and_independent(mode: Mode) -> None:
    source = synthetic("spatial", 4)
    store = ResultStore(mode)
    store.put("key", {"table": source})
    consumer = cast("gpd.GeoDataFrame", store.get("key")["table"])
    consumer.geometry.array[0] = shapely.Point(3, 4)
    assert consumer.geometry.iloc[0] == shapely.Point(3, 4)
    assert_equal(store.get("key")["table"], source)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("kind", ["numeric", "mixed", "spatial"])
def test_graph_matches_unmodified_sdk(mode: Mode, shape: Shape, kind: str) -> None:
    count = 3 if shape == "join" else 1
    workload = build_workload([synthetic(kind, 30) for _ in range(count)], shape)
    configured = make_executor(workload, ResultStore("native"))
    unmodified = GraphExecutor(
        configured.graph, configured.plugins, configured.context_factory
    )
    expected = unmodified.execute(workload.plugin.prepare_invocation("root", {}))

    workload = build_workload([synthetic(kind, 30) for _ in range(count)], shape)
    store = ResultStore(mode)
    executor = make_executor(workload, store)
    actual = executor.run(workload)
    assert_equal(actual["root"], expected["root"])
    assert workload.calls.count("source_0") == 1
    assert store.entries == {}
    expected_counts = {"chain": (7, 7), "fanout": (6, 9), "join": (4, 4)}
    assert (store.stored_tables, store.exported_tables) == expected_counts[shape]


@pytest.mark.parametrize("mode", MODES)
def test_parquet_miss_and_root_hit(tmp_path: Path, mode: Mode) -> None:
    workload = build_workload([synthetic("mixed", 30)], "chain")
    executor = make_executor(workload, ResultStore(mode))
    executor.cache = TableCache(tmp_path)
    expected = executor.run(workload)
    assert len(workload.calls) == 7
    workload = build_workload([synthetic("mixed", 0)], "chain")
    executor = make_executor(workload, ResultStore(mode))
    executor.cache = TableCache(tmp_path)
    actual = executor.run(workload)
    assert_equal(actual["root"], expected["root"])
    assert workload.calls == []
