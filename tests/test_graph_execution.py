from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    Locations,
    MetricParameters,
    PluginDefinition,
    RunContext,
    metric,
)
from lyra.sdk.cache import TableCache
from lyra.sdk.dependencies import LiteralArgument
from lyra.sdk.errors import MetricInputError, MetricResultError
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.local import LocalRunner
from pydantic import Field, field_validator
from shapely.geometry import Point

from tests.location_helpers import fixture_locations, node_contract, output_schema
from tests.test_graph_arguments import geometry

TABLE = pa.DataFrameSchema({})


def executor(
    *handlers: Callable[..., object],
) -> tuple[GraphExecutor, PluginDefinition]:
    # Separate registrations exercise cross-plugin resolution without imports.
    plugins = {
        f"plugin_{index}": PluginDefinition(metrics=[handler])
        for index, handler in enumerate(handlers)
    }
    factory = Mock(side_effect=AssertionError("unexpected context creation"))
    return GraphExecutor(
        LocalRunner.from_definitions(plugins).catalog, plugins, factory
    ), plugins["plugin_0"]


@pytest.mark.parametrize("empty", [False, True])
def test_diamond_multi_output_identity_and_job_local_reuse(
    tmp_path: Path, *, empty: bool
) -> None:
    table_schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})}
    )
    spatial_schema = table_schema.add_columns(
        {
            "geometry": pa.Column(
                "geometry", description="Geometry", metadata={"unit": None}
            )
        }
    )
    trace: list[str] = []
    spatial = gpd.GeoDataFrame(
        {"value": [1]}, geometry=[Point(10, 20)], crs=6372, index=[42]
    )
    plain = pd.DataFrame({"value": [2]}, index=[99])
    if empty:
        spatial = spatial.iloc[:0]
        plain = plain.iloc[:0]

    @metric(name="leaf", description="leaf", outputs={"leaf": table_schema})
    def leaf() -> pd.DataFrame:
        trace.append("leaf")
        return plain

    @metric(
        name="source",
        description="source",
        inputs={"table": Dependency("leaf", producer="leaf")},
        outputs={"places": spatial_schema, "counts": table_schema},
    )
    def source(table: pd.DataFrame) -> dict[str, pd.DataFrame]:
        pd.testing.assert_frame_equal(table, plain.reset_index(drop=True))
        trace.append("source")
        return {"places": spatial, "counts": table}

    @metric(
        name="left",
        description="left",
        inputs={"table": Dependency("places", producer="source")},
        outputs={"left": spatial_schema},
    )
    def left(table: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        pd.testing.assert_frame_equal(table, spatial.reset_index(drop=True))
        assert table.crs == spatial.crs
        assert table.index.equals(pd.RangeIndex(len(spatial)))
        trace.append("left")
        copied = table.copy()
        copied["value"] = 100
        return copied

    @metric(
        name="right",
        description="right",
        inputs={
            "table": Dependency("counts", producer="source"),
            "places": Dependency("places", producer="source"),
        },
        outputs={"right": table_schema},
    )
    def right(table: pd.DataFrame, places: gpd.GeoDataFrame) -> pd.DataFrame:
        pd.testing.assert_frame_equal(table, plain.reset_index(drop=True))
        pd.testing.assert_frame_equal(places, spatial.reset_index(drop=True))
        assert places["value"].tolist() == ([] if empty else [1])
        trace.append("right")
        return table

    @metric(
        name="root",
        description="root",
        inputs={
            "a": Dependency("left", producer="left"),
            "b": Dependency("right", producer="right"),
            "again": Dependency("counts", producer="source"),
        },
        outputs={"summary": spatial_schema, "details": table_schema},
    )
    def root(
        a: pd.DataFrame, b: pd.DataFrame, again: pd.DataFrame
    ) -> dict[str, pd.DataFrame]:
        pd.testing.assert_frame_equal(b, again)
        trace.append("root")
        return {"summary": a, "details": b}

    @metric(name="unused", description="unused", outputs={"unused": TABLE})
    def unused() -> pd.DataFrame:
        pytest.fail("unreachable handler executed")

    runner, plugin = executor(root, left, right, source, leaf, unused)
    for _ in range(2):
        trace.clear()
        result = runner.execute(plugin.prepare_resolved_invocation("root", {})).outputs
        assert set(result) == {"summary", "details"}
        pd.testing.assert_frame_equal(result["details"], plain.reset_index(drop=True))
        assert trace == ["leaf", "source", "left", "right", "root"]
        provenance = runner.provenance
        assert provenance is not None
        assert [
            o.node
            for o in provenance.execution.observations
            if o.outcome == "completed"
        ] == trace
        assert [
            o.node
            for o in provenance.execution.observations
            if o.outcome == "shared_reuse"
        ] == ["source", "source", "source"]
    runner.cache = TableCache(tmp_path)
    runner.execute(plugin.prepare_resolved_invocation("root", {}))
    runner.execute(plugin.prepare_resolved_invocation("root", {}))
    provenance = runner.provenance
    assert provenance is not None
    assert [(o.node, o.outcome) for o in provenance.execution.observations] == [
        ("root", "cache_hit")
    ]


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
        inputs={"table": Dependency("source", producer="source")},
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
                "middle", parameters={"label": " A ", "extra": 1}, producer="middle"
            ),
            "b": Dependency(
                "middle", parameters={"label": "a", "extra": 2}, producer="middle"
            ),
            "c": Dependency("source", parameters={"label": "b"}, producer="source"),
        },
        outputs={"root": TABLE},
    )
    def root(a: pd.DataFrame, b: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
        pd.testing.assert_frame_equal(a, b)
        assert a is not c
        return c

    runner, plugin = executor(root, middle, source)
    runner.execute(plugin.prepare_resolved_invocation("root", {}))
    assert calls == ["a", "b"]


def test_spatial_arguments_forward_unchanged_and_distinguish_invocations() -> None:
    locations: list[Locations] = []

    @metric(
        locations={"location": node_contract()},
        name="source",
        description="source",
        outputs={"source": TABLE},
    )
    def source(location: Locations) -> pd.DataFrame:
        locations.append(location)
        return pd.DataFrame()

    @metric(
        locations={"location": node_contract()},
        name="root",
        description="root",
        inputs={"table": Dependency("source", producer="source")},
        outputs={"root": TABLE},
    )
    def root(location: Locations, table: pd.DataFrame) -> pd.DataFrame:
        assert locations[-1] == location
        return table

    runner, plugin = executor(root, source)
    first = plugin.prepare_resolved_invocation(
        "root",
        {"location": fixture_locations(geometry(), schema=output_schema(label=str))},
    )
    changed = geometry()
    changed["features"][0]["geometry"]["coordinates"][0].reverse()
    second = plugin.prepare_resolved_invocation(
        "root",
        {"location": fixture_locations(changed, schema=output_schema(label=str))},
    )
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
        inputs={"table": Dependency("leaf", producer="leaf")},
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
            "early_table": Dependency("early", producer="early"),
            "table": Dependency("middle", producer="middle"),
            "later": Dependency("leaf", producer="leaf"),
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
    invocation = plugin.prepare_resolved_invocation("root", {"parameters": {}})
    if failure == "arguments":
        # Structurally valid supply path, runtime-invalid override at the deep edge.
        edge = runner.graph.dependencies["middle"]["table"]
        edge.dependency.parameters = {"year": LiteralArgument(value="invalid")}
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
    runner.graph.dependencies["middle"]["table"].dependency.parameters.clear()
    trace.clear()
    assert set(runner.execute(invocation).outputs) == {"root"}
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
        assert not hasattr(context, "db")
        return pd.DataFrame()

    @metric(
        name="root",
        description="root",
        inputs={"table": Dependency("source", producer="source")},
        outputs={"root": TABLE},
    )
    def root(table: pd.DataFrame, context: RunContext) -> pd.DataFrame:
        assert context.node == "root"
        assert context.job_id == "job-123"
        assert not hasattr(context, "db")
        return table

    runner, plugin = executor(root, source)
    runner.context_factory = factory
    invocation = plugin.prepare_resolved_invocation("root", {})
    runner.execute(invocation)
    assert [context.node for context in contexts] == ["source", "root"]


def test_source_can_be_execution_root() -> None:
    @metric(name="source", description="source", outputs={"source": TABLE})
    def source() -> pd.DataFrame:
        return pd.DataFrame()

    runner, plugin = executor(source)
    assert (
        "source"
        in runner.execute(plugin.prepare_resolved_invocation("source", {})).outputs
    )


def test_same_output_from_distinct_producers_executes_and_reuses() -> None:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})}
    )
    calls = []

    @metric(name="first", description="First", outputs={"rows": schema})
    def first() -> pd.DataFrame:
        calls.append("first")
        return pd.DataFrame({"value": [1]})

    @metric(name="second", description="Second", outputs={"rows": schema})
    def second() -> pd.DataFrame:
        calls.append("second")
        return pd.DataFrame({"value": [2]})

    @metric(
        name="consumer",
        description="Consumer",
        outputs={"rows": schema},
        inputs={
            "a": Dependency("rows", producer="first"),
            "b": Dependency("rows", producer="second"),
            "again": Dependency("rows", producer="first"),
        },
    )
    def consumer(a: pd.DataFrame, b: pd.DataFrame, again: pd.DataFrame) -> pd.DataFrame:
        pd.testing.assert_frame_equal(a, again)
        return pd.concat([a, b], ignore_index=True)

    runner, plugin = executor(consumer, first, second)
    result = runner.execute(plugin.prepare_resolved_invocation("consumer", {})).outputs
    assert result["rows"].value.tolist() == [1, 2]
    assert calls == ["first", "second"]
