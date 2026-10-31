from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk.cache import TableCache
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.graph import build_graph

from tests.frame_assertions import (
    assert_equal,
)
from tests.metric_graph import (
    SHAPES,
    Shape,
    Workload,
    build_workload,
    join_tables,
    schema_for,
    synthetic,
    transform,
)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("kind", ["numeric", "mixed", "spatial"])
def test_graph_matches_direct_calculations(shape: Shape, kind: str) -> None:
    count = 3 if shape == "join" else 1
    source = synthetic(kind, 30).reset_index(drop=True)
    if shape == "chain":
        expected = source
        for step in range(6):
            expected = transform(expected, step)
    elif shape == "fanout":
        expected = pd.DataFrame(
            {
                "key": source["key"].to_numpy(),
                "value": sum(
                    transform(source, step)["value"].to_numpy() for step in range(4)
                ),
            }
        )
    else:
        expected = join_tables(source, source, source)

    workload = build_workload([synthetic(kind, 30) for _ in range(count)], shape)
    executor = make_executor(workload)
    actual = executor.execute(workload.plugin.prepare_invocation("root", {}))
    assert_equal(actual["root"], expected)
    assert workload.calls.count("source_0") == 1


def test_cache_miss_and_root_hit(tmp_path: Path) -> None:
    workload = build_workload([synthetic("mixed", 30)], "chain")
    executor = make_executor(workload)
    executor.cache = TableCache(tmp_path)
    expected = executor.execute(workload.plugin.prepare_invocation("root", {}))
    assert len(workload.calls) == 7
    workload = build_workload([synthetic("mixed", 0)], "chain")
    executor = make_executor(workload)
    executor.cache = TableCache(tmp_path)
    actual = executor.execute(workload.plugin.prepare_invocation("root", {}))
    assert_equal(actual["root"], expected["root"])
    assert workload.calls == []


def test_cache_preserves_named_outputs(tmp_path: Path) -> None:
    cache = TableCache(tmp_path)
    first = pd.DataFrame({"first": [1, 2]})
    second = pd.DataFrame({"second": pd.Series(["a", None], dtype="string")})
    with cache.lock("fixture"):
        cache.write(
            "fixture",
            {"left": first, "right": second},
            {"left": schema_for(first), "right": schema_for(second)},
        )
    restored = cache.read("fixture")
    assert restored is not None
    pd.testing.assert_frame_equal(restored["left"], first)
    pd.testing.assert_frame_equal(restored["right"], second)


def make_executor(workload: Workload) -> GraphExecutor:
    return GraphExecutor(
        build_graph({"test": workload.plugin.node_manifests}),
        {"test": workload.plugin},
        Mock(side_effect=AssertionError("Unexpected resource acquisition")),
    )
