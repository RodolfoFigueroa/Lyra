"""Deterministic storage workloads independent of the storage representation."""

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely import affinity

from test_support.workloads import frame

CASES = {
    "typed-scalars": 10_000,
    "varied-points": 10_000,
    "polygon-holes": 1_000,
    "multipolygons": 1_000,
    "complex-small": 16,
    "complex-large": 128,
    "mixed-geometries": 1_200,
}


def _polygon(vertices: int, hole_vertices: int) -> shapely.Polygon:
    outer = shapely.buffer(shapely.Point(0, 0), 0.004, quad_segs=vertices // 4)
    hole = shapely.buffer(shapely.Point(0, 0), 0.001, quad_segs=hole_vertices // 4)
    return shapely.Polygon(outer.exterior.coords, [hole.exterior.coords])


def storage_frame(case: str, *, rows: int | None = None) -> pd.DataFrame:
    """Build one named scenario; row overrides support inexpensive correctness tests."""
    if case not in CASES:
        kind, size = case.split("-")
        return frame(
            rows if rows is not None else (1000 if size == "small" else 10000), kind
        )
    count = CASES[case] if rows is None else rows
    values = np.arange(count)
    result = frame(count)
    result["count"] = pd.array(values, dtype="Int64")
    result["flag"] = pd.array(values % 2 == 0, dtype="boolean")
    result["category"] = pd.Categorical.from_codes(
        values % 3, pd.Index(["a", "b", "c", "unused"]), ordered=True
    )
    result["timestamp"] = pd.date_range(
        "2024-01-01", periods=count, freq="ns", tz="UTC"
    )
    result.loc[result.index[::11], ["count", "flag", "category", "timestamp"]] = None
    if case == "typed-scalars":
        return result
    if case == "varied-points":
        return gpd.GeoDataFrame(
            result,
            geometry=shapely.points(
                -100 + (values % 100) * 0.05, 30 + (values // 100) * 0.05
            ),
            crs=4326,
        )
    polygon = _polygon(32, 8)
    multipart = shapely.MultiPolygon(
        [affinity.translate(polygon, xoff=i * 0.012) for i in range(3)]
    )
    line = shapely.LineString([(i * 0.0001, (i % 3) * 0.0001) for i in range(16)])
    mixed = [
        shapely.Point(0, 0),
        shapely.MultiPoint([(0, 0), (0.001, 0.001)]),
        line,
        shapely.MultiLineString([line, affinity.translate(line, yoff=0.002)]),
        polygon,
        multipart,
    ]
    base = _polygon(1024, 64) if case.startswith("complex-") else _polygon(64, 16)
    geometries = []
    for i in range(count):
        geometry = (
            mixed[i % 6]
            if case == "mixed-geometries"
            else multipart
            if case == "multipolygons"
            else mixed[0]
            if case == "varied-points"
            else base
        )
        geometry = affinity.scale(
            geometry, xfact=1 + (i % 7) / 20, yfact=1 + (i % 5) / 20, origin=(0, 0)
        )
        geometry = affinity.translate(
            geometry, xoff=-100 + (i % 100) * 0.05, yoff=30 + (i // 100) * 0.05
        )
        if case == "mixed-geometries" and (i // 6) % 2:
            geometry = shapely.force_3d(geometry, z=float(i % 13))
        geometries.append(geometry)
    return gpd.GeoDataFrame(result, geometry=geometries, crs=4326)
