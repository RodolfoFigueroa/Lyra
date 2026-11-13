"""Shared production regression fixtures and assertions."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import geopandas as gpd
import numpy as np
import pandas as pd
import pandera.pandas as pa
import shapely
from geopandas import GeoDataFrame

from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import fixture_locations
from test_support.workloads import schema as native_schema
from tests.location_checks import rich_payload, rich_schema
from tests.location_graph import workload
from tests.storage_data import frame


@dataclass(frozen=True)
class Case:
    """A single independently varied workload."""

    name: str
    rows: int
    kind: str
    vertices: int = 0
    width: int = 1


@dataclass
class Fixture:
    """Independent declared input and optional location value."""

    case: Case
    frame: pd.DataFrame
    schema: pa.DataFrameSchema
    value: GeoDataFrame | None
    output: LocationOutputSchema | None

    def build(self) -> GeoDataFrame:
        """Return a fresh, unhashed location snapshot.

        Raises:
            ValueError: If the fixture is a metric table.
        """
        if self.output is None:
            message = "Native fixtures have no location output"
            raise ValueError(message)
        return self.output.prepare_frame(cast("gpd.GeoDataFrame", self.frame))


def fixture(case: Case, seed: int = 914) -> Fixture:
    """Return declared values without consulting inferred output dtypes."""
    if case.kind == "rich":
        output = rich_schema()
        value = fixture_locations(rich_payload(case.rows), schema=output)
        return Fixture(case, value.copy(deep=True), output.schema, value, output)
    base = frame().drop(columns="category")
    for name, dtype in {
        "small": "Int64",
        "unsigned": "string",
        "single": "Float64",
        "time": "string",
        "date": "string",
    }.items():
        base[name] = base[name].astype(dtype)
    schema = native_schema(base)
    source = base.iloc[np.arange(case.rows) % len(base)].reset_index(drop=True)
    if case.width > 1:
        source = pd.concat(
            [source.add_suffix(f"_{i}") for i in range(case.width)], axis=1
        )
        schema = pa.DataFrameSchema(
            {
                f"{name}_{i}": column
                for i in range(case.width)
                for name, column in schema.columns.items()
            },
            strict=True,
        )
    output = None
    if case.kind != "native":
        rng = np.random.default_rng(seed)
        points = shapely.points(
            rng.uniform(-100, 100, case.rows), rng.uniform(-50, 50, case.rows)
        )
        geometry = (
            shapely.buffer(points, 0.01, quad_segs=case.vertices // 4)
            if case.kind == "polygons"
            else points
        )
        source = gpd.GeoDataFrame(source, geometry=geometry, crs=4326)
        source["id"] = pd.array([f"row-{i}" for i in range(case.rows)], dtype="str")
        schema = schema.add_columns(
            {"geometry": pa.Column("geometry"), "id": pa.Column(str)}
        )
    schema = native_schema(source)
    for name, column in schema.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    schema.validate(source)
    if case.kind != "native":
        output = LocationOutputSchema(schema)
    result = Fixture(case, source, schema, None, output)
    if output is not None:
        result.value = result.build()
    return result


def graph(
    data: Fixture, shape: str, directory: Path | None
) -> tuple[LocalRunner, list[str]]:
    """Return real SDK graphs whose handlers contain no benchmark assertions."""
    if data.value is not None:
        return workload(
            data.value, shape, directory, output=data.output, check_contents=False
        )
    calls: list[str] = []

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"rows": data.schema},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        return data.frame.copy(deep=True)

    @metric(
        cache="full",
        code_revision="1",
        name="step",
        description="Step",
        outputs={"forwarded": data.schema},
        inputs={"rows": Dependency("rows", producer="source")},
    )
    def step(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("step")
        return rows

    count_schema = pa.DataFrameSchema(
        {"count": pa.Column(int, description="Count", metadata={"unit": None})},
        strict=True,
    )
    binding = "rows" if shape == "fanout" else "forwarded"

    @metric(
        cache="full",
        code_revision="1",
        name="left",
        description="Left",
        outputs={"left": count_schema},
        inputs={
            "rows": Dependency(
                binding, producer="source" if shape == "fanout" else "step"
            )
        },
    )
    def left(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("left")
        return pd.DataFrame({"count": [len(rows)]})

    @metric(
        cache="full",
        code_revision="1",
        name="right",
        description="Right",
        outputs={"right": count_schema},
        inputs={
            "rows": Dependency(
                binding, producer="source" if shape == "fanout" else "step"
            )
        },
    )
    def right(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("right")
        return pd.DataFrame({"count": [len(rows)]})

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"root": count_schema},
        inputs={
            "a": Dependency("left", producer="left"),
            "b": Dependency(
                "left" if shape == "chain" else "right",
                producer="left" if shape == "chain" else "right",
            ),
        },
    )
    def root(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return a + b

    return LocalRunner.from_definitions(
        {"benchmark": PluginDefinition(metrics=[source, step, left, right, root])},
        artifact_dir=directory,
    ), calls
