"""Offline export timings; invoke separately for each workload and revision."""

import argparse
import json
import platform
import resource
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter

import geopandas as gpd
import numpy as np
import shapely
from lyra.sdk import Locations
from lyra.sdk.geometry_shapely import frame_geometries
from lyra.sdk.models.location import location_snapshot

from benchmarks.location_baseline.fixtures import assert_exact
from tests.location_helpers import output_schema


def fixture(rows: int, kind: str) -> Locations:
    """Create deterministic owned inputs outside measurement.

    Returns:
        Locations containing points, dense polygons, or mixed geometries.
    """
    points = np.asarray(shapely.points(np.arange(rows) % 1000, np.arange(rows) // 1000))
    shapes = (
        points
        if kind == "points"
        else np.asarray(shapely.buffer(points, 0.4, quad_segs=64))
    )
    if kind == "mixed":
        shapes[::2] = points[::2]
    return Locations.from_geodataframe(
        gpd.GeoDataFrame(
            {"id": [str(i) for i in range(rows)], "value": np.arange(rows)},
            geometry=shapes,
            crs=4326,
        ),
        collection="benchmark:export",
        unit_type="shape",
        schema=output_schema(value=int),
    )


def main() -> None:
    """Measure one workload in a fresh process and write machine-readable results."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--kind", choices=("polygons", "points", "mixed"), default="polygons"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    value = fixture(args.rows, args.kind)
    snapshot = location_snapshot(value)
    records = {}
    for name, action in {
        "geometry": lambda: frame_geometries(snapshot),
        "frame": value.to_geodataframe,
        "four_exports": lambda: [value.to_geodataframe() for _ in range(4)],
    }.items():
        action()
        timings = []
        for _ in range(args.repeats):
            start = perf_counter()
            result = action()
            timings.append(perf_counter() - start)
            del result
        records[name] = {"seconds": timings, "median": median(timings)}
    result = value.to_geodataframe()
    assert_exact(result["id"].tolist(), value.ids)
    assert_exact(
        location_snapshot(
            Locations.from_geodataframe(
                result.astype({"value": "int64"}),
                collection=value.collection,
                unit_type=value.unit_type,
                schema=output_schema(value=int),
            )
        ).fingerprint,
        snapshot.fingerprint,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "rows": args.rows,
                "repeats": args.repeats,
                "kind": args.kind,
                "python": platform.python_version(),
                "versions": {
                    name: version(name)
                    for name in ("numpy", "shapely", "pyarrow", "geopandas")
                },
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "measurements": records,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
