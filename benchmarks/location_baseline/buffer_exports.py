"""Offline buffer/export measurements, one operation per process."""

import argparse
import gc
import json
import os
import platform
import resource
import threading
from collections.abc import Callable, Iterator
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from lyra.sdk import (
    LocationOutputSchema,
    Locations,
    geometry_buffers,
    location_artifacts,
)
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import location_snapshot

from tests.location_helpers import fixture_locations, output_schema, producer_result

if TYPE_CHECKING:
    import geopandas as gpd
    from lyra.sdk.models.geometry import Feature

from benchmarks.location_baseline.fixtures import assert_exact
from benchmarks.location_baseline.frame_conversion import fixture


def _rss() -> int:
    return int(
        Path("/proc/self/statm").read_text(encoding="ascii").split()[1]
    ) * os.sysconf("SC_PAGE_SIZE")


def measure_memory(action: Callable[[], object]) -> tuple[int, int]:
    """Return baseline and peak RSS while retaining the action result."""
    baseline = _rss()
    samples = [baseline]
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(0.005):
            samples.append(_rss())

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        result = action()
        samples.append(_rss())
        del result
    finally:
        stop.set()
        thread.join()
    return baseline, max(samples)


def _schema(kind: str) -> LocationOutputSchema:
    if kind != "properties":
        return output_schema(value=int)
    schema = output_schema(**{f"field_{j}": "Float64" for j in range(16)})
    for name, column in schema.schema.columns.items():
        if name.startswith("field_"):
            column.nullable = True
    return schema


def _fixture(rows: int, kind: str) -> Locations:
    if kind != "properties":
        return fixture(rows, kind)
    scalars = (1.0, None, 2**40 + 0.5, -0.0, 3.0, 2.5)
    return fixture_locations(
        {
            "collection": "benchmark:properties",
            "unit_type": "point",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(i),
                    "geometry": {"type": "Point", "coordinates": [float(i), 0.0]},
                    "properties": {
                        f"field_{j}": scalars[(i + j) % len(scalars)] for j in range(16)
                    },
                }
                for i in range(rows)
            ],
        },
        schema=_schema(kind),
    )


def _expected_rows(snapshot: LocationSnapshot) -> Iterator[dict[str, Any]]:
    for start in range(0, snapshot.geometry.num_rows, 256):
        data = snapshot.slice(start, 256).property_data()
        geometries = geometry_buffers.decode(snapshot.geometry.slice(start, 256))
        for identifier, properties, geometry in zip(
            data["ids"], data["properties"], geometries, strict=True
        ):
            yield {
                "type": "Feature",
                "id": identifier,
                "properties": properties,
                "geometry": geometry.model_dump(),
            }


def _check(operation: str, checked: object, snapshot: LocationSnapshot) -> None:
    if operation in {"snapshot", "artifact"}:
        assert_exact(
            cast("LocationSnapshot", checked).fingerprint, snapshot.fingerprint
        )
    elif operation == "from_frame":
        assert_exact(
            location_snapshot(cast("Locations", checked)).fingerprint,
            snapshot.fingerprint,
        )
    elif operation == "count":
        assert_exact(checked, (snapshot.table.num_rows, True, False))
    elif operation == "first_id":
        assert_exact(checked, snapshot.table.column(0)[0].as_py())
    elif operation == "preview":
        assert_exact(checked, snapshot.to_dict(limit=5))
    elif operation == "first_batch":
        assert_exact(
            cast("Feature", checked).model_dump(),
            snapshot.to_dict(limit=1)["features"][0],
        )
    elif operation in {"features", "dictionary", "json"}:
        exported = (
            (feature.model_dump() for feature in cast("list[Feature]", checked))
            if operation == "features"
            else (
                json.loads(cast("str", checked))
                if operation == "json"
                else cast("dict[str, Any]", checked)
            )["features"]
        )
        for actual, expected in zip(exported, _expected_rows(snapshot), strict=True):
            assert_exact(actual, expected)


def main() -> None:
    """Record repeat timings and process memory for a deterministic workload."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--kind",
        choices=("points", "polygons", "mixed", "properties"),
        default="polygons",
    )
    parser.add_argument(
        "--operation",
        choices=(
            "count",
            "first_id",
            "first_property",
            "projection",
            "first_batch",
            "preview",
            "snapshot",
            "features",
            "dictionary",
            "json",
            "artifact",
            "geometry_validation",
            "property_validation",
            "from_frame",
        ),
        required=True,
    )
    parser.add_argument(
        "--memory", action="store_true", help="Run a separate RSS sampling pass"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1:
        parser.error("rows and repeats must be positive")
    value = _fixture(args.rows, args.kind)
    snapshot = location_snapshot(value)
    encoded = (
        location_artifacts.encode(snapshot) if args.operation == "artifact" else b""
    )
    schema = _schema(args.kind)
    frame = producer_result(value, schema) if args.operation == "from_frame" else None
    actions = {
        "count": lambda: (len(value), bool(value), value.is_empty),
        "first_id": lambda: value.ids[0],
        "first_property": lambda: value.property(value.property_names[0])[0],
        "projection": lambda: value.to_dataframe(properties=value.property_names[:1]),
        "first_batch": lambda: next(value.iter_features(batch_size=64)),
        "preview": lambda: value.to_dict(limit=5),
        "geometry_validation": lambda: geometry_buffers.validate(snapshot.geometry),
        "property_validation": lambda: snapshot.table.validate(full=True),
        "from_frame": lambda: Locations.from_geodataframe(
            cast("gpd.GeoDataFrame", frame),
            collection=value.collection,
            unit_type=value.unit_type,
            schema=schema,
        ),
        "snapshot": lambda: LocationSnapshot(
            snapshot.table, snapshot.geometry, snapshot.scope
        ),
        "features": value.to_features,
        "dictionary": snapshot.to_dict,
        "json": value.to_json,
        "artifact": lambda: location_artifacts.decode(encoded),
    }
    setup_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    times = []
    for _ in range(args.repeats):
        gc.collect()
        start = perf_counter()
        result = actions[args.operation]()
        times.append(perf_counter() - start)
        del result
    gc.collect()
    baseline, peak = (
        measure_memory(actions[args.operation]) if args.memory else (None, None)
    )
    measured_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    checked = actions[args.operation]()
    _check(args.operation, checked, snapshot)
    del checked
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "rows": args.rows,
                "repeats": args.repeats,
                "kind": args.kind,
                "operation": args.operation,
                "memory_sampled": args.memory,
                "seconds": times,
                "median": median(times),
                "setup_peak_rss_kib": setup_peak,
                "operation_baseline_rss_bytes": baseline,
                "operation_peak_rss_bytes": peak,
                "peak_rss_kib": measured_peak,
                "python": platform.python_version(),
                "versions": {
                    name: version(name)
                    for name in ("numpy", "pyarrow", "pydantic", "shapely")
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
