"""Repeatable synthetic workloads shared by benchmarks and isolated tests."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pa
import shapely

from lyra.contracts.config import PluginsConfig
from lyra.contracts.plugins import PluginInfo
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server.plugins import InstalledPlugin
from lyra.server.registry import Catalog


def frame(rows: int, kind: str = "mixed") -> pd.DataFrame:
    """Return deterministic scalar or polygon rows with nullable attributes."""
    values = np.arange(rows)
    result = pd.DataFrame({"id": values.astype(str), "value": values * 0.125})
    result["label"] = pd.array([f"group-{i % 17}" for i in values], dtype="string")
    result.loc[result.index[::11], "label"] = pd.NA
    if kind == "polygon":
        return gpd.GeoDataFrame(
            result,
            geometry=shapely.buffer(shapely.points(values % 100, values // 100), 0.2),
            crs=4326,
        )
    return result


def schema(table: pd.DataFrame) -> pa.DataFrameSchema:
    """Return an explicit schema preserving the fixture's types."""
    return pa.DataFrameSchema(
        {
            str(name): pa.Column(
                dtype, nullable=True, description=str(name), metadata={"unit": None}
            )
            for name, dtype in table.dtypes.items()
        }
    )


@dataclass
class Workload:
    """Reusable plugin and bounded invocation counters."""

    plugin: PluginDefinition
    calls: dict[str, int]


def workload(rows: int, shape: str = "chain", nodes: int = 6) -> Workload:
    """Return a chain, shared-source diamond, or independent catalog nodes."""
    table = frame(rows)
    contract = schema(table)
    calls: dict[str, int] = {}
    handlers: list[Callable[..., object]] = []

    def source(name: str) -> None:
        @metric(
            name=name,
            description=f"Population measurement {name}",
            outputs={name: contract},
        )
        def produce() -> pd.DataFrame:
            calls[name] = calls.get(name, 0) + 1
            return table

        handlers.append(produce)

    def step(name: str, parent: str) -> None:
        @metric(
            name=name,
            description=name,
            inputs={"data": Dependency(parent, producer=parent)},
            outputs={name: contract},
        )
        def calculate(data: pd.DataFrame) -> pd.DataFrame:
            calls[name] = calls.get(name, 0) + 1
            return data.assign(value=data["value"] + 1)

        handlers.append(calculate)

    source("source")
    if shape == "catalog":
        for i in range(1, nodes):
            step(f"node_{i}", "source")
    elif shape == "diamond":
        for i in range(4):
            step(f"branch_{i}", "source")

        handlers.append(_combine(contract, calls))
    else:
        parent = "source"
        for i in range(nodes):
            name = "root" if i == nodes - 1 else f"step_{i}"
            step(name, parent)
            parent = name
    return Workload(PluginDefinition(metrics=handlers), calls)


def installed(plugin: PluginDefinition) -> list[InstalledPlugin]:
    """Return installed metadata without accessing a distribution or service."""
    return [
        InstalledPlugin(
            "benchmark-fixture",
            plugin.manifest(
                plugin=PluginInfo(name="benchmark-fixture", version="1"),
                factory="test_support.workloads:workload",
            ),
            dict.fromkeys(plugin.metric_names, "default"),
        )
    ]


def catalog_declarations(sizes: list[int]) -> dict[int, list[InstalledPlugin]]:
    """Build serializable installed declarations for each requested graph size."""
    return {nodes: installed(workload(1, "catalog", nodes).plugin) for nodes in sizes}


def catalog(plugins: list[InstalledPlugin]) -> Catalog:
    """Return a compiled catalog with installation lookup substituted."""
    with patch("lyra.server.registry.load_installed_plugins", return_value=plugins):
        return Catalog.load(
            PluginsConfig(default_queue="default", allowed_queues=["default"])
        )


def _combine(
    contract: pa.DataFrameSchema, calls: dict[str, int]
) -> "Callable[..., object]":
    """Return a four-branch aggregation handler."""

    @metric(
        name="root",
        description="Combine shared branches",
        inputs={
            f"b{i}": Dependency(f"branch_{i}", producer=f"branch_{i}") for i in range(4)
        },
        outputs={"root": contract},
    )
    def combine(
        b0: pd.DataFrame, b1: pd.DataFrame, b2: pd.DataFrame, b3: pd.DataFrame
    ) -> pd.DataFrame:
        calls["root"] = calls.get("root", 0) + 1
        return b0.assign(value=b0.value + b1.value + b2.value + b3.value)

    return combine
