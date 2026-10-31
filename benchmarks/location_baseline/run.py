"""Measure public location boundaries and real offline graph execution."""

import argparse
import json
import platform
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import ClassVar, cast

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
from lyra.sdk.arguments import PreparedResolvedInvocation
from lyra.sdk.cache import TableCache
from lyra.sdk.geometry_buffers import Geometry as BufferGeometry
from lyra.sdk.geometry_buffers import decode, encode
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from pandera.engines.geopandas_engine import Geometry

from benchmarks.location_baseline.fixtures import assert_exact, payload
from benchmarks.metric_representation.execution import peak_mib


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


def workload(
    value: Locations,
    shape: str,
    directory: Path | None,
    *,
    output: LocationOutputSchema | None = None,
) -> tuple:
    """Build a fake source and handlers using the real executor and cache.

    Returns:
        Runner and mutable handler trace.
    """
    calls = []
    output = output or schema()

    def result(source: Locations) -> gpd.GeoDataFrame:
        frame = source.to_geodataframe()
        for name, column in output.schema.columns.items():
            if name != output.geometry_column:
                frame[name] = column.dtype.coerce(frame[name])
        return frame

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
            assert_exact(
                (collection, unit_type, ids),
                (self.collections[0].name, value.unit_type, value.ids),
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
                    ids=value.ids,
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

    @metric(
        name="left",
        description="Left",
        outputs={"left": table_schema},
    )
    def left(location: Locations) -> pd.DataFrame:
        calls.append("left")
        assert_exact(location, value)
        return pd.DataFrame({"count": [len(location.ids)]})

    @metric(
        name="right",
        description="Right",
        outputs={"right": table_schema},
    )
    def right(location: Locations) -> pd.DataFrame:
        calls.append("right")
        assert_exact(location, value)
        return pd.DataFrame({"count": [len(location.ids)]})

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


def measure(rows: int, repeats: int) -> dict:
    """Measure complete operations; compare outputs outside boundary timings.

    Returns:
        Raw timings, process high-water memory, cache sizes, and handler counts.
    """
    raw = payload(rows)
    # Geometry-only 2D points are representable through current schema conversion.
    for feature in raw["features"]:
        feature["properties"] = {}
        feature["geometry"] = {"type": "Point", "coordinates": [1.0, 2.0]}
    value = Locations.model_validate(raw)
    output = schema()
    geometries = [f["geometry"] for f in payload(rows)["features"]]
    encoded = encode(geometries)
    records = []
    with TemporaryDirectory() as scratch:
        cache = TableCache(Path(scratch) / "boundary")
        cache.directory.mkdir()
        frame = value.to_geodataframe()
        actions = {
            "geometry_encode": lambda: encode(geometries),
            "geometry_decode": lambda: decode(encoded),
            "construction": lambda: Locations.model_validate(raw),
            "validation": lambda: output.validate(value),
            "features": value.model_dump,
            "to_frame": value.to_geodataframe,
            "from_frame": lambda: Locations.from_geodataframe(
                frame,
                collection=value.collection,
                unit_type=value.unit_type,
                id_column="id",
            ),
            "invocation": lambda: (
                PreparedResolvedInvocation("root", None, {"location": value}).identity
            ),
            "write": lambda: cache.write_locations("value", value),
            "read": lambda: cache.read_locations("value"),
        }
        for repeat in range(repeats):
            for name, action in actions.items():
                start = perf_counter()
                result = action()
                seconds = perf_counter() - start
                if name.startswith("geometry_"):
                    assert_exact(
                        [
                            g.model_dump()
                            for g in (
                                decode(result)
                                if name == "geometry_encode"
                                else cast("list[BufferGeometry]", result)
                            )
                        ],
                        geometries,
                    )
                elif name in {
                    "construction",
                    "validation",
                    "from_frame",
                    "read",
                    "features",
                }:
                    assert_exact(
                        result, value if name != "features" else value.model_dump()
                    )
                elif name == "to_frame":
                    pd.testing.assert_frame_equal(
                        cast("pd.DataFrame", result), frame, check_exact=True
                    )
                records.append(
                    {
                        "operation": name,
                        "repeat": repeat,
                        "seconds": seconds,
                        "peak_mib": peak_mib(),
                    }
                )
            for shape in ("chain", "fanout", "shared"):
                directory = Path(scratch) / f"{shape}-{repeat}"
                for mode in ("off", "empty", "populated"):
                    runner, calls = workload(
                        value, shape, None if mode == "off" else directory
                    )
                    start = perf_counter()
                    result = runner.run("root", {})
                    seconds = perf_counter() - start
                    pd.testing.assert_frame_equal(
                        result["root"], pd.DataFrame({"count": [2 * rows]})
                    )
                    records.append(
                        {
                            "operation": shape,
                            "cache": mode,
                            "repeat": repeat,
                            "seconds": seconds,
                            "peak_mib": peak_mib(),
                            "handlers": calls,
                            "artifact_bytes": sum(
                                p.stat().st_size
                                for p in directory.rglob("*")
                                if p.is_file()
                            ),
                        }
                    )
    return {
        "rows": rows,
        "repeats": repeats,
        "python": platform.python_version(),
        "versions": {
            name: version(name)
            for name in (
                "pandas",
                "geopandas",
                "shapely",
                "pydantic",
                "pandera",
                "pyarrow",
            )
        },
        "samples": records,
    }


def main() -> None:
    """Write repeatable offline measurements as JSON."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    args.output.write_text(
        json.dumps(measure(args.rows, args.repeats), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
