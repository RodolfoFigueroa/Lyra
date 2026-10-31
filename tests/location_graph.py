"""Shared production regression fixtures and assertions."""

from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from lyra.sdk import (
    Dependency,
    Input,
    IOConfig,
    LocalRunner,
    LocationCollection,
    LocationDependency,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    LocationSelection,
    PluginDefinition,
    location_operation,
    metric,
    resolve_locations,
)
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from pandera.engines.geopandas_engine import Geometry

from tests.location_data import assert_exact


def schema() -> LocationOutputSchema:
    """Return the geometry-only workload contract."""
    return LocationOutputSchema(
        pa.DataFrameSchema(
            {
                "id": pa.Column(str, description="ID", metadata={"unit": None}),
                "geometry": pa.Column(
                    Geometry, description="Geometry", metadata={"unit": None}
                ),
            }
        )
    )


def _result_frame(
    source: Locations, output: LocationOutputSchema, *, coerce: bool
) -> gpd.GeoDataFrame:
    frame = source.to_geodataframe()
    if coerce:
        for name, column in output.schema.columns.items():
            if name != output.geometry_column:
                frame[name] = column.dtype.coerce(frame[name])
    return frame


def _compare(actual: object, expected: object, *, enabled: bool) -> None:
    if enabled:
        assert_exact(actual, expected)


def workload(
    value: Locations,
    shape: str,
    directory: Path | None,
    *,
    output: LocationOutputSchema | None = None,
    check_contents: bool = True,
) -> tuple:
    """Build a fake source and handlers using the real executor and cache.

    Returns:
        Runner and mutable handler trace.
    """
    calls = []
    output = output or schema()

    def result(source: Locations) -> gpd.GeoDataFrame:
        return _result_frame(source, output, coerce=check_contents)

    class Provider(LocationProvider[IOConfig]):
        collections = (
            LocationCollection(
                name=value.collection, unit_types=(value.unit_type,), crs="EPSG:4326"
            ),
        )
        outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
            (value.collection, value.unit_type): output
        }

        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            _compare(
                (collection, unit_type, ids),
                (self.collections[0].name, value.unit_type, list(value.ids)),
                enabled=check_contents,
            )
            calls.append("provider")
            return result(value)

    selected = SelectedLocationProvider(
        "benchmark",
        LocationProviderManifest(
            resource="locations", collections=Provider.collections
        ),
    )

    @location_operation(
        name="source",
        description="Source",
        output=output,
        collection=value.collection,
        unit_type=value.unit_type,
    )
    def source() -> gpd.GeoDataFrame:
        calls.append("source")
        resolved = resolve_locations(
            {
                "location": LocationSelection(
                    collection=value.collection,
                    unit_type=value.unit_type,
                    ids=list(value.ids),
                )
            },
            selected=selected,
            resources={"locations": Provider},
        )["location"]
        return result(resolved)

    @location_operation(
        name="step", description="Step", output=output, scope_from=Input("location")
    )
    def step(location: Locations) -> gpd.GeoDataFrame:
        calls.append("step")
        return result(location)

    table_schema = pa.DataFrameSchema(
        {"count": pa.Column(int, description="Count", metadata={"unit": None})}
    )
    binding = (
        LocationDependency("step", locations={"location": LocationDependency("source")})
        if shape in {"chain", "shared"}
        else LocationDependency("source")
    )

    def consume(location: Locations, name: str) -> pd.DataFrame:
        calls.append(name)
        if check_contents:
            assert_exact(location, value)
        return pd.DataFrame({"count": [len(location.ids)]})

    @metric(
        name="left",
        description="Left",
        outputs={"left": table_schema},
    )
    def left(location: Locations) -> pd.DataFrame:
        return consume(location, "left")

    @metric(
        name="right",
        description="Right",
        outputs={"right": table_schema},
    )
    def right(location: Locations) -> pd.DataFrame:
        return consume(location, "right")

    @metric(
        name="root",
        description="Root",
        outputs={"root": table_schema},
        inputs={
            "a": Dependency("left", locations={"location": binding}),
            "b": Dependency(
                "left" if shape == "chain" else "right", locations={"location": binding}
            ),
        },
    )
    def root(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return a + b

    plugin = PluginDefinition(
        metrics=[left, right, root], location_operations=[source, step]
    )
    return LocalRunner.from_definitions(
        {"benchmark": plugin}, cache_dir=directory
    ), calls
