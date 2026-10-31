"""Shared production regression fixtures and assertions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pa
import shapely
from lyra.sdk import Dependency, PluginDefinition, metric

if TYPE_CHECKING:
    from collections.abc import Callable

Shape = Literal["chain", "fanout", "join"]
SHAPES: tuple[Shape, ...] = ("chain", "fanout", "join")


def synthetic(kind: str, rows: int) -> pd.DataFrame:
    """Build deterministic wide, mixed, or spatial metric-like inputs.

    Returns:
        A table exercising the selected representation.

    Raises:
        ValueError: If the fixture kind is unknown.
    """
    number = np.arange(rows)
    frame = pd.DataFrame(
        {"key": number, "group": number % 100, "value": number * 0.125}
    )
    if kind == "numeric":
        for column in range(29):
            frame[f"measure_{column}"] = np.sin(number + column)
    elif kind == "mixed":
        frame["nullable"] = pd.array(number, dtype="Int64")
        frame.loc[number % 11 == 0, "nullable"] = pd.NA
        frame["flag"] = pd.array(number % 2 == 0, dtype="boolean")
        frame.loc[number % 13 == 0, "flag"] = pd.NA
        frame["label"] = pd.array(
            [f"label-{value % 10000}" for value in number], dtype="string[python]"
        )
        frame["category"] = pd.Categorical.from_codes(
            number % 3, categories=pd.Index(["a", "b", "c", "unused"]), ordered=True
        )
        frame["timestamp"] = pd.to_datetime(number, unit="s", utc=True)
        frame.index = pd.MultiIndex.from_arrays(
            [frame["category"], number], names=["category_index", "row"]
        )
    elif kind == "spatial":
        frame = gpd.GeoDataFrame(
            frame,
            geometry=shapely.buffer(
                shapely.points(number % 1000, number // 1000), 0.4, quad_segs=16
            ),
            crs=3857,
        )
    else:
        msg = f"Unknown fixture: {kind}"
        raise ValueError(msg)
    return frame


def schema_for(frame: pd.DataFrame) -> pa.DataFrameSchema:
    """Validate each output's complete column contract through Lyra.

    Returns:
        An explicit schema with no coercion.
    """
    return pa.DataFrameSchema(
        {
            str(name): pa.Column(
                dtype, nullable=True, description=str(name), metadata={"unit": None}
            )
            for name, dtype in frame.dtypes.items()
        },
        strict=True,
    )


def transform(table: pd.DataFrame, step: int) -> pd.DataFrame:
    """Run selection, grouped aggregation, arithmetic, and optional geometry work.

    Returns:
        A derived table preserving the input schema and index.
    """
    result = table.copy(deep=False)
    mean = table.groupby("group", observed=True)["value"].transform("mean")
    result["value"] = table["value"] * 1.001 + mean + step
    result.loc[result["key"] % 11 == 0, "value"] = 0.0
    if isinstance(table, gpd.GeoDataFrame):
        result["value"] += table.geometry.area
    return result


def join_tables(
    left: pd.DataFrame, right: pd.DataFrame, third: pd.DataFrame
) -> pd.DataFrame:
    """Join three complete inputs.

    Returns:
        A narrow numeric result joined on unique keys.
    """
    result = left[["key", "value"]].merge(
        right[["key", "value"]], on="key", suffixes=("_left", "_right")
    )
    result = result.merge(third[["key", "value"]], on="key")
    result["value"] += result.pop("value_left") + result.pop("value_right")
    return result


@dataclass
class Workload:
    """A validated plugin graph with single-use sources and an invocation trace."""

    plugin: PluginDefinition
    calls: list[str]


def build_workload(frames: list[pd.DataFrame], shape: Shape) -> Workload:
    """Create a chain of six transforms, four-way fan-out, or three-input join.

    Sources release their input references when called so the benchmark does not
    retain a hidden second copy of every producer's data.

    Returns:
        The workload's registered handlers and their invocation trace.
    """
    schema = schema_for(frames[0])
    calls: list[str] = []
    handlers: list[Callable[..., object]] = []

    def add_source(name: str, frame: pd.DataFrame) -> None:
        pending = [frame]

        @metric(name=name, description=name, outputs={name: schema})
        def source() -> pd.DataFrame:
            calls.append(name)
            return pending.pop()

        handlers.append(source)

    for index, frame in enumerate(frames):
        add_source(f"source_{index}", frame)
    frames.clear()

    def add_step(name: str, parent: str, step: int) -> None:
        @metric(
            name=name,
            description=name,
            inputs={"table": Dependency(parent)},
            outputs={name: schema},
        )
        def calculation(table: pd.DataFrame) -> pd.DataFrame:
            calls.append(name)
            return transform(table, step)

        handlers.append(calculation)

    summary = schema_for(pd.DataFrame({"key": [0], "value": [0.0]}))
    if shape == "chain":
        parent = "source_0"
        for step in range(6):
            name = "root" if step == 5 else f"step_{step}"
            add_step(name, parent, step)
            parent = name
    elif shape == "fanout":
        for step in range(4):
            add_step(f"branch_{step}", "source_0", step)

        handlers.append(_fanout_root(summary, calls))
    else:
        handlers.append(_joined_root(summary, calls))
    return Workload(PluginDefinition(metrics=handlers), calls)


def _fanout_root(schema: pa.DataFrameSchema, calls: list[str]) -> Callable[..., object]:
    @metric(
        name="root",
        description="Aggregate four branches",
        inputs={f"t{i}": Dependency(f"branch_{i}") for i in range(4)},
        outputs={"root": schema},
    )
    def root(
        t0: pd.DataFrame,
        t1: pd.DataFrame,
        t2: pd.DataFrame,
        t3: pd.DataFrame,
    ) -> pd.DataFrame:
        calls.append("root")
        return pd.DataFrame(
            {
                "key": t0["key"].to_numpy(),
                "value": sum(t["value"].to_numpy() for t in (t0, t1, t2, t3)),
            }
        )

    return root


def _joined_root(schema: pa.DataFrameSchema, calls: list[str]) -> Callable[..., object]:
    @metric(
        name="root",
        description="Join three inputs",
        inputs={
            "left": Dependency("source_0"),
            "right": Dependency("source_1"),
            "third": Dependency("source_2"),
        },
        outputs={"root": schema},
    )
    def root(
        left: pd.DataFrame, right: pd.DataFrame, third: pd.DataFrame
    ) -> pd.DataFrame:
        calls.append("root")
        return join_tables(left, right, third)

    return root
