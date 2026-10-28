from collections.abc import Callable
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk import (
    Dependency,
    LocationInput,
    MetricParameters,
    PluginDefinition,
    RunContext,
    metric,
)
from lyra.sdk.dependencies import LiteralArgument
from lyra.sdk.errors import MetricInputError, MetricResultError
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import build_graph
from lyra.sdk.models.plugin import PluginInfo, TableOutput
from pydantic import Field, field_validator
from shapely.geometry import Point

from tests.test_graph_arguments import geometry

TABLE = TableOutput(columns=[])


def executor(
    *handlers: Callable[..., object],
) -> tuple[GraphExecutor, PluginDefinition]:
    # Separate registrations exercise cross-plugin resolution without imports.
    plugins = {
        f"plugin_{index}": PluginDefinition(nodes=[handler])
        for index, handler in enumerate(handlers)
    }
    graph = build_graph(
        {
            name: plugin.manifest(
                plugin=PluginInfo(name=name, version="1"), factory="fake:create"
            ).nodes
            for name, plugin in plugins.items()
        }
    )
    factory = Mock(side_effect=AssertionError("unexpected context creation"))
    return GraphExecutor(graph, plugins, factory), plugins["plugin_0"]


@pytest.mark.parametrize("empty", [False, True])
def test_diamond_multi_output_identity_and_job_local_reuse(*, empty: bool) -> None:
    trace: list[str] = []
    spatial = gpd.GeoDataFrame(
        {"value": [1]}, geometry=[Point(10, 20)], crs=6372, index=[42]
    )
    plain = pd.DataFrame({"value": [2]}, index=[99])
    if empty:
        spatial = spatial.iloc[:0]
        plain = plain.iloc[:0]

    @metric(name="leaf", description="leaf", outputs={"leaf": TABLE})
    def leaf() -> pd.DataFrame:
        trace.append("leaf")
        return plain

    @metric(
        name="source",
        description="source",
        inputs={"table": Dependency("leaf")},
        outputs={"places": TABLE, "counts": TABLE},
    )
    def source(table: pd.DataFrame) -> dict[str, pd.DataFrame]:
        assert table is plain
        trace.append("source")
        return {"places": spatial, "counts": table}

    @metric(
        name="left",
        description="left",
        inputs={"table": Dependency("places")},
        outputs={"left": TABLE},
    )
    def left(table: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        assert table is spatial
        assert table.crs == spatial.crs
        assert table.index.equals(spatial.index)
        trace.append("left")
        copied = table.copy()
        copied["value"] = 100
        return copied

    @metric(
        name="right",
        description="right",
        inputs={"table": Dependency("counts"), "places": Dependency("places")},
        outputs={"right": TABLE},
    )
    def right(table: pd.DataFrame, places: gpd.GeoDataFrame) -> pd.DataFrame:
        assert table is plain
        assert places is spatial
        assert places["value"].tolist() == ([] if empty else [1])
        trace.append("right")
        return table

    @metric(
        name="root",
        description="root",
        inputs={
            "a": Dependency("left"),
            "b": Dependency("right"),
            "again": Dependency("counts"),
        },
        outputs={"summary": TABLE, "details": TABLE},
    )
    def root(
        a: pd.DataFrame, b: pd.DataFrame, again: pd.DataFrame
    ) -> dict[str, pd.DataFrame]:
        assert b is again is plain
        trace.append("root")
        return {"summary": a, "details": b}

    @metric(name="unused", description="unused", outputs={"unused": TABLE})
    def unused() -> pd.DataFrame:
        pytest.fail("unreachable handler executed")

    runner, plugin = executor(root, left, right, source, leaf, unused)
    for _ in range(2):
        trace.clear()
        result = runner.execute(plugin.prepare_invocation("root", {}))
        assert set(result) == {"summary", "details"}
        assert result["details"] is plain
        assert trace == ["leaf", "source", "left", "right", "root"]


class Label(MetricParameters):
    label: str = Field(default="a", description="Label")

    @field_validator("label")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.strip().lower()


class Consumer(Label):
    extra: int = Field(default=1, description="Consumer only")


def test_effective_arguments_control_reuse() -> None:
    calls: list[str] = []

    @metric(name="source", description="source", outputs={"source": TABLE})
    def source(parameters: Label) -> pd.DataFrame:
        calls.append(parameters.label)
        return pd.DataFrame()

    @metric(
        name="middle",
        description="middle",
        inputs={"table": Dependency("source")},
        outputs={"middle": TABLE},
    )
    def middle(parameters: Consumer, table: pd.DataFrame) -> pd.DataFrame:
        assert parameters.extra in {1, 2}
        return table

    @metric(
        name="root",
        description="root",
        inputs={
            "a": Dependency(
                "middle", arguments={"parameters": {"label": " A ", "extra": 1}}
            ),
            "b": Dependency(
                "middle", arguments={"parameters": {"label": "a", "extra": 2}}
            ),
            "c": Dependency("source", arguments={"parameters": {"label": "b"}}),
        },
        outputs={"root": TABLE},
    )
    def root(a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
        assert a is b
        assert a is not c
        return c

    runner, plugin = executor(root, middle, source)
    runner.execute(plugin.prepare_invocation("root", {}))
    assert calls == ["a", "b"]


def test_spatial_arguments_forward_unchanged_and_distinguish_invocations() -> None:
    locations: list[LocationInput] = []

    @metric(name="source", description="source", outputs={"source": TABLE})
    def source(location: LocationInput) -> pd.DataFrame:
        locations.append(location)
        return pd.DataFrame()

    @metric(
        name="root",
        description="root",
        inputs={"table": Dependency("source")},
        outputs={"root": TABLE},
    )
    def root(location: LocationInput, table: pd.DataFrame) -> pd.DataFrame:
        assert locations[-1] is location
        return table

    runner, plugin = executor(root, source)
    first = plugin.prepare_invocation("root", {"location": geometry()})
    changed = geometry()
    changed["features"][0]["geometry"]["coordinates"][0].reverse()
    second = plugin.prepare_invocation("root", {"location": changed})
    assert first.identity != second.identity
    runner.execute(first)
    runner.execute(second)
    assert len(locations) == 2
    assert locations[0] != locations[1]


class Year(MetricParameters):
    year: int = Field(default=2020, description="Year")


@pytest.mark.parametrize("failure", ["handler", "arguments", "result"])
def test_deep_failures_stop_execution_and_next_run_starts_fresh(failure: str) -> None:
    trace: list[str] = []
    broken = True
    original = RuntimeError("source unavailable")

    @metric(name="leaf", description="leaf", outputs={"leaf": TABLE})
    def leaf(parameters: Year) -> object:
        trace.append("leaf")
        assert parameters.year == 2020
        if broken and failure == "handler":
            raise original
        return None if broken and failure == "result" else pd.DataFrame()

    @metric(
        name="middle",
        description="middle",
        inputs={"table": Dependency("leaf")},
        outputs={"middle": TABLE},
    )
    def middle(parameters: Year, table: pd.DataFrame) -> pd.DataFrame:
        trace.append("middle")
        assert parameters.year == 2020
        return table

    @metric(name="early", description="early", outputs={"early": TABLE})
    def early() -> pd.DataFrame:
        trace.append("early")
        return pd.DataFrame()

    @metric(
        name="root",
        description="root",
        inputs={
            "early_table": Dependency("early"),
            "table": Dependency("middle"),
            "later": Dependency("leaf"),
        },
        outputs={"root": TABLE},
    )
    def root(
        parameters: Year,
        early_table: pd.DataFrame,
        table: pd.DataFrame,
        later: pd.DataFrame,
    ) -> pd.DataFrame:
        trace.append("root")
        assert parameters.year == 2020
        assert early_table.empty
        assert later.empty
        return table

    runner, plugin = executor(root, middle, leaf, early)
    invocation = plugin.prepare_invocation("root", {"parameters": {}})
    if failure == "arguments":
        # Structurally valid supply path, runtime-invalid override at the deep edge.
        edge = runner.graph.dependencies["middle"]["table"]
        edge.dependency.arguments["parameters"] = {
            "year": LiteralArgument(value="invalid")
        }
    with pytest.raises(GraphExecutionError) as error:
        runner.execute(invocation)
    assert error.value.node == "leaf"
    assert error.value.path == ("root", "middle", "leaf")
    assert "root -> middle -> leaf" in str(error.value)
    assert trace == (["early"] if failure == "arguments" else ["early", "leaf"])
    if failure == "handler":
        assert error.value.__cause__ is original
    else:
        assert isinstance(
            error.value.__cause__,
            MetricInputError if failure == "arguments" else MetricResultError,
        )
    broken = False
    runner.graph.dependencies["middle"]["table"].dependency.arguments.clear()
    trace.clear()
    assert set(runner.execute(invocation)) == {"root"}
    assert trace == ["early", "leaf", "middle", "root"]


def test_context_identity_and_job_association() -> None:
    contexts: list[RunContext] = []

    def factory(name: str) -> RunContext:
        context = Mock(spec=RunContext)
        context.node = name
        context.job_id = "job-123"
        contexts.append(context)
        return context

    @metric(name="source", description="source", outputs={"source": TABLE})
    def source(context: RunContext) -> pd.DataFrame:
        assert context.node == "source"
        assert context.job_id == "job-123"
        assert context.db is not None
        return pd.DataFrame()

    @metric(
        name="root",
        description="root",
        inputs={"table": Dependency("source")},
        outputs={"root": TABLE},
    )
    def root(table: pd.DataFrame, context: RunContext) -> pd.DataFrame:
        assert context.node == "root"
        assert context.job_id == "job-123"
        assert context.db is not None
        return table

    runner, plugin = executor(root, source)
    runner.context_factory = factory
    invocation = plugin.prepare_invocation("root", {})
    runner.execute(invocation)
    assert [context.node for context in contexts] == ["source", "root"]


def test_source_can_be_execution_root() -> None:
    @metric(name="source", description="source", outputs={"source": TABLE})
    def source() -> pd.DataFrame:
        return pd.DataFrame()

    runner, plugin = executor(source)
    assert "source" in runner.execute(plugin.prepare_invocation("source", {}))
