"""Shared production regression fixtures and assertions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from geopandas import GeoDataFrame
from pandera.engines.geopandas_engine import Geometry
from typing_extensions import override

from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.location_schema import LocationOutputSchema
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, location_operation, metric


def schema() -> LocationOutputSchema:
    """Return the geometry-only workload contract."""
    return LocationOutputSchema(
        pa.DataFrameSchema(
            {
                "id": pa.Column(str, description="ID", metadata={"unit": None}),
                "geometry": pa.Column(
                    Geometry(crs=4326),
                    checks=pa.Check(
                        lambda s: s.geom_type.isin(
                            [
                                "Point",
                                "Polygon",
                                "MultiPolygon",
                                "MultiPoint",
                                "LineString",
                                "MultiLineString",
                                "GeometryCollection",
                            ]
                        ),
                        name="geometry_types",
                    ),
                    description="Geometry",
                    metadata={"unit": None},
                ),
            },
            strict=True,
        )
    )


def workload_inputs(value: GeoDataFrame) -> dict[str, object]:
    """Select the fixture provider's records explicitly."""
    return {"location": {"provider": "benchmark_records", "ids": value["id"].tolist()}}


@dataclass(frozen=True)
class WorkloadOptions:
    """Control fixture checks and consumer identities for cache measurements."""

    cache: Literal["full", "off"] = "full"
    check_contents: bool = True
    consumer_revision: str = "1"


def workload(
    value: GeoDataFrame,
    shape: str,
    directory: Path | None,
    *,
    output: LocationOutputSchema | None = None,
    options: WorkloadOptions | None = None,
) -> tuple[LocalRunner, list[str]]:
    """Return a native provider workload and its handler/acquisition trace."""
    options = options or WorkloadOptions()
    calls: list[str] = []
    output = output or schema()
    native_schema = output.schema.rename_columns({output.id_column: "location_id"})
    native_schema.strict = True
    native_schema.columns["location_id"].nullable = False
    native_schema.columns["geometry"].nullable = False
    native_schema.columns["location_id"].unique = True
    native_schema.columns["location_id"].checks = [
        pa.Check(lambda s: s.str.len().gt(0), name="nonempty")
    ]
    native_schema.columns["geometry"].dtype = Geometry(crs=str(value.crs))
    native_schema.columns["geometry"].checks = [
        pa.Check(lambda s: s.is_valid, name="valid")
    ]
    expected = value.copy(deep=True).rename(columns={output.id_column: "location_id"})

    # Storage fixtures include invalid topology for lossless WKB checks. Providers
    # expose valid records, so repair that topology before entering this workload.
    expected.geometry = expected.geometry.make_valid()

    class Provider(LocationProvider):
        id = "benchmark_records"
        schema = native_schema

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            calls.append("provider")
            return expected.loc[expected.location_id.isin(ids)].copy()

    @location_operation(
        cache=options.cache,
        name="source",
        code_revision="1",
        description="Source",
        inputs={"location": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def source(location: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append("source")
        return {key: (key,) for key in location.location_id}

    @location_operation(
        cache=options.cache,
        name="step",
        code_revision="1",
        description="Step",
        inputs={"location": LocationDependency(provider=Provider.id)},
        target_provider=Provider.id,
    )
    def step(location: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        calls.append("step")
        return {key: (key,) for key in location.location_id}

    table_schema = pa.DataFrameSchema(
        {"count": pa.Column(int, description="Count", metadata={"unit": None})},
        strict=True,
    )
    binding = (
        LocationDependency("step").bind(location=LocationDependency("source"))
        if shape in {"chain", "shared"}
        else LocationDependency("source")
    )

    def consume(location: gpd.GeoDataFrame, name: str) -> pd.DataFrame:
        calls.append(name)
        if options.check_contents:
            pd.testing.assert_frame_equal(
                location.loc[:, expected.columns], expected, check_exact=True
            )
        return pd.DataFrame({"count": [len(location)]})

    @metric(
        cache=options.cache,
        name="left",
        code_revision=options.consumer_revision,
        description="Left",
        inputs={"location": LocationDependency(provider=Provider.id)},
        outputs={"left": table_schema},
    )
    def left(location: gpd.GeoDataFrame) -> pd.DataFrame:
        return consume(location, "left")

    @metric(
        cache=options.cache,
        name="right",
        code_revision=options.consumer_revision,
        description="Right",
        inputs={"location": LocationDependency(provider=Provider.id)},
        outputs={"right": table_schema},
    )
    def right(location: gpd.GeoDataFrame) -> pd.DataFrame:
        return consume(location, "right")

    @metric(
        cache=options.cache,
        name="root",
        code_revision=options.consumer_revision,
        description="Root",
        outputs={"root": table_schema},
        inputs={
            "location": LocationDependency(provider=Provider.id),
            "a": Dependency("left", producer="left").bind(location=binding),
            "b": Dependency(
                "left" if shape == "chain" else "right",
                producer="left" if shape == "chain" else "right",
            ).bind(location=binding),
        },
    )
    def root(
        location: gpd.GeoDataFrame, a: pd.DataFrame, b: pd.DataFrame
    ) -> pd.DataFrame:
        calls.append("root")
        assert len(location) == len(value)
        return a + b

    plugin = PluginDefinition(
        metrics=[left, right, root],
        location_operations=[source, step],
        resources={"records": Provider},
    )
    return LocalRunner.from_definitions(
        {"benchmark": plugin},
        result_store_path=directory / "results.sqlite3"
        if directory is not None
        else None,
    ), calls
