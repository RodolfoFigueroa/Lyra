"""Offline single-pass construction measurements, one workload per process."""

import argparse
import gc
import json
import platform
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import ClassVar, NoReturn

import geopandas as gpd
import numpy as np
import pandera.pandas as pa
import shapely
from lyra.sdk import (
    IOConfig,
    LocationCollection,
    LocationOutputSchema,
    LocationProvider,
    Locations,
    LocationSelection,
    PluginDefinition,
    location_operation,
)
from lyra.sdk.location_resolution import resolve_locations
from lyra.sdk.locations import LocationProviderManifest, SelectedLocationProvider
from lyra.sdk.models.location import location_fingerprint
from pandera.engines.geopandas_engine import Geometry

from benchmarks.location_baseline.buffer_exports import measure_memory
from benchmarks.location_baseline.fixtures import assert_exact


def fixture(rows: int, kind: str) -> tuple[gpd.GeoDataFrame, LocationOutputSchema]:
    """Return native producer data and its declared schema."""
    points = np.asarray(shapely.points(np.arange(rows) % 1000, np.arange(rows) // 1000))
    shapes = (
        points
        if kind in {"points", "properties"}
        else np.asarray(shapely.buffer(points, 0.4, quad_segs=64))
    )
    if kind == "mixed":
        shapes[::2] = points[::2]
    data = {"id": [str(i) for i in range(rows)]}
    data.update(
        {
            f"value_{i}": np.arange(rows, dtype=float)
            for i in range(16 if kind == "properties" else 1)
        }
    )
    frame = gpd.GeoDataFrame(data, geometry=shapes, crs=4326)
    schema = LocationOutputSchema(
        pa.DataFrameSchema(
            {
                name: pa.Column(dtype, description=name, metadata={"unit": None})
                for name, dtype in {
                    "id": str,
                    "geometry": Geometry,
                    **{key: float for key in data if key != "id"},
                }.items()
            }
        )
    )
    return frame, schema


def measure(rows: int, kind: str, operation: str, repeats: int) -> dict:
    """Measure an isolated producer boundary and verify content identity.

    Returns:
        Repeat timings, incremental memory, versions and exact parity status.

    Raises:
        AssertionError: If constructed contents differ from the fixture.
    """
    frame, schema = fixture(rows, kind)
    collection = "benchmark:ingestion"
    unit = "shape"
    expected = location_fingerprint(
        Locations.from_geodataframe(
            frame, collection=collection, unit_type=unit, id_column="id"
        )
    )
    reversed_frame = frame.iloc[::-1]

    @location_operation(
        name="make",
        description="Native frame",
        output=schema,
        collection=collection,
        unit_type=unit,
    )
    def make() -> gpd.GeoDataFrame:
        return frame

    class Provider(LocationProvider[IOConfig]):
        collections = (
            LocationCollection(name=collection, unit_types=(unit,), crs="EPSG:4326"),
        )
        outputs: ClassVar = {(collection, unit): schema}

        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            if (collection, unit_type) not in self.outputs:
                raise ValueError((collection, unit_type))
            with self.operation("resolve"):
                assert_exact(ids, frame.id.tolist())
            return reversed_frame

    plugin = PluginDefinition(location_operations=[make])
    invocation = plugin.prepare_resolved_invocation("make", {})
    selected = SelectedLocationProvider(
        "benchmark",
        LocationProviderManifest(
            resource="locations", collections=Provider.collections
        ),
    )
    selection = LocationSelection(
        collection=collection, unit_type=unit, ids=frame.id.tolist()
    )

    def no_context(_node: str) -> NoReturn:
        msg = "Context-free benchmark unexpectedly acquired context"
        raise AssertionError(msg)

    def action() -> Locations:
        if operation == "import":
            return Locations.from_geodataframe(
                frame, collection=collection, unit_type=unit, id_column="id"
            )
        if operation == "operation":
            return plugin.invoke_operation(invocation, {}, no_context)
        return resolve_locations(
            {"source": selection}, selected=selected, resources={"locations": Provider}
        )["source"]

    timings = []
    for _ in range(repeats):
        gc.collect()
        start = perf_counter()
        result = action()
        timings.append(perf_counter() - start)
        if location_fingerprint(result) != expected:
            msg = "Constructed location contents differ from the fixture"
            raise AssertionError(msg)
        del result
    gc.collect()
    baseline, peak = measure_memory(action)
    return {
        "rows": rows,
        "kind": kind,
        "operation": operation,
        "seconds": timings,
        "median": median(timings),
        "baseline_rss_bytes": baseline,
        "peak_rss_bytes": peak,
        "incremental_peak_rss_bytes": peak - baseline,
        "parity": True,
        "python": platform.python_version(),
        "versions": {
            name: version(name)
            for name in (
                "numpy",
                "pandas",
                "shapely",
                "geopandas",
                "pyarrow",
                "pandera",
            )
        },
    }


def main() -> None:
    """Write one construction benchmark result as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=64200)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--kind", choices=("points", "polygons", "mixed", "properties"), required=True
    )
    parser.add_argument(
        "--operation", choices=("import", "operation", "provider"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    result = measure(args.rows, args.kind, args.operation, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
