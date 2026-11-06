from typing import Any

RING = [[0, 0], [2, 0], [2, 2], [0, 0]]


GEOMETRIES = {
    "Point": [0, 1],
    "MultiPoint": [[0, 1], [2, 3]],
    "LineString": [[0, 1], [2, 3]],
    "MultiLineString": [[[0, 1], [2, 3]], [[4, 5], [6, 7]]],
    "Polygon": [RING],
    "MultiPolygon": [[RING]],
}


def station_collection(kinds: list[str]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": f"000{i}:station",
                "type": "Feature",
                "geometry": {"type": kind, "coordinates": GEOMETRIES[kind]},
                "properties": {"label": kind, "reading": i},
            }
            for i, kind in enumerate(kinds)
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:27700"}},
    }


def projected_collection(*geometry_types: str) -> dict[str, Any]:
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    coordinates = {"Point": [0, 0], "Polygon": [ring], "MultiPolygon": [[ring]]}
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": str(index),
                "geometry": {"type": kind, "coordinates": coordinates[kind]},
                "properties": {"label": "retained"},
            }
            for index, kind in enumerate(geometry_types)
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:6372"}},
    }


VALUE_GEOMETRIES = GEOMETRIES | {
    "Point": [0, 1, 2],
    "MultiLineString": [[[0, 1], [2, 3]]],
}
